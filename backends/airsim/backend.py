"""AirSim Backend mapped through the simulator_contract adapter.

P2 supports platform move_to/hover only. reset performs takeoff; cleanup
returns home and lands. Neither method is a platform Action in contract v0.1.
"""
from __future__ import annotations

import math
from pathlib import Path
import time
from typing import Callable
from uuid import uuid4

from contracts import (
    Action, ActionKind, BackendConfig, CleanupResult, ContractError,
    ExecutionResult, PlatformContractException, PlatformObservation,
    PositionNed, SensorReference, TaskSpec,
)
from simulator_contract.contracts import Capability, ErrorCode, PoseNed, SensorKind, SensorRequest, SimulatorContractException
from simulator_contract.legacy_airsim import AirSimLegacyAdapter


def _number(options: dict, name: str, default: float, *, minimum: float = 0.0) -> float:
    value = options.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= minimum:
        raise PlatformContractException(ContractError("invalid_argument", f"connection.{name} must be finite and greater than {minimum}"))
    return float(value)


def _error(error: object, fallback: str = "simulator") -> ContractError:
    if isinstance(error, SimulatorContractException):
        source = error.error
        return ContractError(source.code.value, source.message, source.retryable)
    source = getattr(error, "error", None)
    if source is not None:
        code = source.code.value if isinstance(source.code, ErrorCode) else str(source.code)
        return ContractError(code, source.message, source.retryable)
    return ContractError(fallback, str(error))


class AirSimBackend:
    """One vehicle, external simulator process, world NED metres."""

    def __init__(self, adapter_factory: Callable[..., AirSimLegacyAdapter] = AirSimLegacyAdapter) -> None:
        self._factory = adapter_factory
        self._adapter: AirSimLegacyAdapter | None = None
        self._config: BackendConfig | None = None
        self._task: TaskSpec | None = None
        self._options: dict = {}
        self._sequence = 0
        self._home: PositionNed | None = None
        self._last_state = None
        self._collision_guard_active = False
        self.runtime_metadata: dict[str, object] = {}

    def reset(self, config: BackendConfig, task: TaskSpec) -> PlatformObservation:
        self.close()
        if config.backend_type != "airsim":
            raise PlatformContractException(ContractError("invalid_argument", "AirSimBackend requires backend_type=airsim"))
        options = dict(config.connection)
        host = options.get("host", "127.0.0.1")
        port = options.get("port", 41451)
        if not isinstance(host, str) or not host or isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise PlatformContractException(ContractError("invalid_argument", "invalid AirSim host or port"))
        if task.altitude_reference != "relative_to_home":
            raise PlatformContractException(ContractError("invalid_argument", "AirSimBackend requires relative_to_home altitude"))
        self._options = options
        self._config, self._task, self._home = config, task, task.home_position_ned
        self._adapter = self._factory(host=host, port=port)
        self.runtime_metadata = {"state_confirmed_rpc_false": []}
        started = time.monotonic()
        try:
            self._check(self._adapter.connect(_number(options, "connect_timeout_s", 5.0)), "connect")
            self._check(self._adapter.reset_vehicle(), "reset")
            self._check(self._adapter.set_api_control(self._rpc_vehicle_id(), True), "api_control")
            self._check(self._adapter.arm(self._rpc_vehicle_id(), True), "arm")
            staging = options.get("spawn_position_ned")
            if staging is not None:
                if not isinstance(staging, dict):
                    raise PlatformContractException(ContractError("invalid_argument", "connection.spawn_position_ned must be an object"))
                position = PositionNed.from_dict(staging)
                measured = self._state().pose_ned
                self._check(self._adapter.set_vehicle_pose(self._rpc_vehicle_id(), PoseNed(
                    position.north_m, position.east_m, position.down_m,
                    measured.orientation_w, measured.orientation_x,
                    measured.orientation_y, measured.orientation_z,
                )), "set_vehicle_pose")
                self._wait_for(
                    lambda state: self._distance(state.pose_ned, position) <= _number(options, "home_tolerance_m", 2.0),
                    _number(options, "spawn_confirm_timeout_s", 3.0),
                    "staging position was not confirmed",
                )
                # Teleporting near the road briefly induces contact motion in UE4.
                time.sleep(_number(options, "spawn_settle_delay_s", 1.0))
                self._wait_for(
                    lambda state: math.hypot(*state.velocity_ned_mps) <=
                    _number(options, "spawn_speed_tolerance_mps", 0.03),
                    _number(options, "spawn_settle_timeout_s", 5.0),
                    "staging position did not settle before takeoff",
                )
            ground = self._state()
            home_error = self._distance(ground.pose_ned, self._home)
            if home_error > _number(options, "home_tolerance_m", 2.0):
                raise PlatformContractException(ContractError("precondition_failed", f"reset position differs from task home by {home_error:.2f} m"))
            if options.get("takeoff_on_reset", True) is not True:
                raise PlatformContractException(ContractError("invalid_argument", "P2 AirSimBackend requires takeoff_on_reset=true"))
            takeoff = self._adapter.takeoff(self._rpc_vehicle_id(), _number(options, "takeoff_timeout_s", 20.0))
            height = _number(options, "takeoff_min_height_m", 0.3)
            self._confirm_command_by_state(
                takeoff, "takeoff", "takeoff",
                lambda state: self._home.down_m - state.pose_ned.down_m >= height and state.landed is not True,
                _number(options, "takeoff_confirm_timeout_s", 10.0),
                "takeoff was not confirmed by altitude and landed state",
            )
            self._sequence = 0
            self.runtime_metadata.update({
                "backend": "airsim", "simulator_id": self._adapter.simulator_id,
                "vehicle_id": config.vehicle_id, "host": host, "port": port,
                "reset_duration_s": time.monotonic() - started,
                "declared_capabilities": sorted(item.value for item in self._adapter.capabilities(self._rpc_vehicle_id())),
                "world_frame": "NED", "takeoff_on_reset": True,
            })
            return self.observe()
        except Exception as exc:
            try:
                if self._adapter is not None:
                    self.cleanup()
            except Exception:
                pass
            error = exc if isinstance(exc, PlatformContractException) else PlatformContractException(_error(exc))
            self.close()
            raise error from exc if error is not exc else None

    def observe(self) -> PlatformObservation:
        if self._adapter is None or self._config is None:
            raise PlatformContractException(ContractError("not_connected", "AirSimBackend has no active session"))
        try:
            state = self._state()
            sensors: list[SensorReference] = []
            missing: dict[str, str] = {}
            requests = self._options.get("sensors", [])
            if not isinstance(requests, list):
                raise PlatformContractException(ContractError("invalid_argument", "connection.sensors must be a list"))
            for item in requests:
                if not isinstance(item, dict) or not isinstance(item.get("sensor_id"), str) or item.get("kind") not in ("rgb", "depth"):
                    raise PlatformContractException(ContractError("invalid_argument", "each sensor requires sensor_id and rgb/depth kind"))
                sensor_id, kind_name = item["sensor_id"], item["kind"]
                key = f"{kind_name}:{sensor_id}"
                capability = Capability.RGB_CAMERA if kind_name == "rgb" else Capability.DEPTH_CAMERA
                if capability not in self._adapter.capabilities(self._rpc_vehicle_id()):
                    missing[key] = "not_supported"
                    continue
                if self._config.resource_root is None:
                    missing[key] = "resource_root_not_configured"
                    continue
                try:
                    reading = self._adapter.observe(self._rpc_vehicle_id(), SensorRequest(sensor_id, SensorKind(kind_name)))
                    if not reading.payload:
                        missing[key] = "empty_payload"
                        continue
                    suffix = "png" if kind_name == "rgb" else "f32"
                    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in sensor_id)
                    sensor_dir = Path(self._config.resource_root) / "sensors"
                    sensor_dir.mkdir(parents=True, exist_ok=True)
                    while True:
                        relative = f"sensors/{self._sequence:06d}_{kind_name}_{safe_id}_{uuid4().hex}.{suffix}"
                        destination = Path(self._config.resource_root) / relative
                        try:
                            with destination.open("xb") as output:
                                output.write(reading.payload)
                            break
                        except FileExistsError:
                            # A new capture must never replace an earlier reference.
                            continue
                    sensors.append(SensorReference(sensor_id, kind_name, relative, reading.metadata.wall_time_ns))
                except (OSError, SimulatorContractException) as exc:
                    missing[key] = _error(exc).code
            return PlatformObservation(
                self._sequence, self._config.vehicle_id,
                PositionNed(state.pose_ned.north_m, state.pose_ned.east_m, state.pose_ned.down_m),
                state.velocity_ned_mps, state.wall_time_ns, state.simulator_time_ns,
                tuple(sensors), missing,
            )
        except PlatformContractException:
            raise
        except Exception as exc:
            raise PlatformContractException(_error(exc)) from exc

    def plot_goal(self, target: PositionNed) -> None:
        if self._adapter is None:
            raise PlatformContractException(ContractError("not_connected", "AirSimBackend has no active session"))
        try:
            self._adapter.plot_goal(PoseNed(target.north_m, target.east_m, target.down_m))
        except Exception as exc:
            raise PlatformContractException(_error(exc)) from exc

    def collision_info(self) -> dict[str, object]:
        if self._adapter is None:
            raise PlatformContractException(ContractError("not_connected", "AirSimBackend has no active session"))
        try:
            raw = self._adapter.collision_info(self._rpc_vehicle_id())
            return {"has_collided": raw["has_collided"],
                    "time_stamp": raw.get("time_stamp")}
        except Exception as exc:
            raise PlatformContractException(_error(exc)) from exc

    def execute(self, action: Action) -> ExecutionResult:
        now = time.time_ns()
        if self._adapter is None or self._config is None:
            return ExecutionResult(action.action_id, False, True, False, now, ContractError("not_connected", "AirSimBackend has no active session"))
        if action.vehicle_id != self._config.vehicle_id:
            return ExecutionResult(action.action_id, False, True, False, now, ContractError("invalid_argument", "action vehicle_id does not match backend vehicle"))
        self._sequence += 1
        started = time.monotonic()
        try:
            if action.kind is ActionKind.HOVER:
                self._confirm_command_by_state(
                    self._adapter.hover(self._rpc_vehicle_id()), "hover", "hover",
                    lambda state: math.hypot(*state.velocity_ned_mps) <= _number(self._options, "hover_speed_tolerance_mps", 0.5),
                    max(0.001, action.deadline_s - (time.monotonic() - started)),
                    "hover speed was not confirmed",
                )
            elif action.kind is ActionKind.MOVE_TO:
                self._collision_guard_active = self._options.get("abort_on_collision", False) is True
                try:
                    self._move_to(action.target_position_ned, max(0.001, action.deadline_s - (time.monotonic() - started)))
                finally:
                    self._collision_guard_active = False
            else:
                return ExecutionResult(action.action_id, False, True, False, time.time_ns(), ContractError("not_supported", f"unsupported action {action.kind}"))
            if time.monotonic() - started > action.deadline_s:
                raise PlatformContractException(ContractError("timeout", "action completed after its deadline"))
            return ExecutionResult(action.action_id, True, True, True, time.time_ns())
        except Exception as exc:
            return ExecutionResult(action.action_id, True, True, False, time.time_ns(), _error(exc))

    def _move_to(self, target: PositionNed, timeout_s: float,
                 position_tolerance_m: float | None = None) -> None:
        assert self._adapter is not None and self._config is not None
        deadline = time.monotonic() + timeout_s
        speed = _number(self._options, "move_speed_mps", 1.0)
        tolerance = (position_tolerance_m if position_tolerance_m is not None
                     else _number(self._options, "position_tolerance_m", 0.75))
        interval = _number(self._options, "control_interval_s", 0.25)
        while True:
            state = self._state()
            if self._collision_guard_active and self.collision_info()["has_collided"]:
                raise PlatformContractException(ContractError(
                    "collision", "AirSim reported a collision during move_to"))
            dx = target.north_m - state.pose_ned.north_m
            dy = target.east_m - state.pose_ned.east_m
            dz = target.down_m - state.pose_ned.down_m
            distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            if time.monotonic() > deadline:
                raise PlatformContractException(ContractError("timeout", f"move_to deadline elapsed; remaining {distance:.2f} m"))
            if distance <= tolerance:
                self._confirm_command_by_state(
                    self._adapter.hover(self._rpc_vehicle_id()), "hover_at_target", "hover",
                    lambda settled: self._distance(settled.pose_ned, target) <= tolerance
                    and math.hypot(*settled.velocity_ned_mps) <= _number(self._options, "hover_speed_tolerance_mps", 0.5),
                    max(0.001, deadline - time.monotonic()),
                    "target hover was not confirmed",
                )
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PlatformContractException(ContractError("timeout", f"move_to target not reached; remaining {distance:.2f} m"))
            duration = min(interval, remaining, distance / speed)
            scale = min(speed / distance, 1.0 / duration)
            vn, ve, vd = dx * scale, dy * scale, dz * scale
            q = state.pose_ned
            yaw = math.atan2(2 * (q.orientation_w * q.orientation_z + q.orientation_x * q.orientation_y),
                             1 - 2 * (q.orientation_y ** 2 + q.orientation_z ** 2))
            forward = math.cos(yaw) * vn + math.sin(yaw) * ve
            right = -math.sin(yaw) * vn + math.cos(yaw) * ve
            self._check(self._adapter.move_body_velocity(self._rpc_vehicle_id(), forward, right, vd, duration, rpc_timeout_s=max(0.05, deadline - time.monotonic())), "move_body_velocity")

    def cleanup(self) -> CleanupResult:
        if self._adapter is None or self._config is None or self._home is None:
            return CleanupResult(False, None)
        errors: list[ContractError] = []
        try:
            state = self._state()
            if state.landed is not True and self._home.down_m - state.pose_ned.down_m > 0.3:
                flight_down = min(state.pose_ned.down_m, self._home.down_m - _number(self._options, "return_height_m", 1.0))
                self._move_to(
                    PositionNed(self._home.north_m, self._home.east_m, flight_down),
                    _number(self._options, "return_timeout_s", 30.0),
                    _number(self._options, "return_position_tolerance_m", 0.5),
                )
        except Exception as exc:
            errors.append(_error(exc))
        try:
            self.runtime_metadata["landing_confirmation"] = "pending"
            self._check(self._adapter.land(self._rpc_vehicle_id(), _number(self._options, "land_timeout_s", 20.0)), "land")
            try:
                self._wait_for(lambda state: state.landed is True,
                               _number(self._options, "land_confirm_timeout_s", 3.0),
                               "landed state not confirmed")
                self.runtime_metadata["landing_confirmation"] = "landed_state"
            except PlatformContractException:
                # This legacy scene can report Flying while stationary on the road.
                near = self._state()
                if not self._near_ground_stationary(near):
                    result = self._adapter.move_to_z(
                        self._rpc_vehicle_id(), self._home.down_m - 0.15, 0.75, 20.0
                    )
                    if not result.accepted and not self._near_ground_stationary(self._state()):
                        self._check(result, "near_ground")
                    self._check(self._adapter.land(self._rpc_vehicle_id(), 20.0), "land_retry")
                    try:
                        self._wait_for(lambda state: state.landed is True, 3.0,
                                       "landed state not confirmed")
                        self.runtime_metadata["landing_confirmation"] = "landed_state_after_retry"
                    except PlatformContractException:
                        near = self._state()
                if self.runtime_metadata["landing_confirmation"] == "pending":
                    if not self._near_ground_stationary(near):
                        raise PlatformContractException(ContractError(
                            "timeout", "landing was not confirmed by landed state or near-ground stationary state"
                        ))
                    self.runtime_metadata["landing_confirmation"] = "near_ground_disarmed_compatibility"
        except Exception as exc:
            errors.append(_error(exc))
        # Release control even when landing confirmation fails, so a failed
        # fallback cannot strand an armed vehicle in the simulator.
        try:
            self._check(self._adapter.arm(self._rpc_vehicle_id(), False), "disarm")
        except Exception as exc:
            errors.append(_error(exc))
        try:
            self._check(self._adapter.set_api_control(self._rpc_vehicle_id(), False), "release_api_control")
        except Exception as exc:
            errors.append(_error(exc))
        if errors:
            return CleanupResult(True, False, ContractError(errors[0].code, "; ".join(e.message for e in errors)))
        return CleanupResult(True, True)

    def close(self) -> None:
        """Idempotent connection release; never performs implicit flight cleanup."""
        if self._adapter is not None:
            self._adapter.close()
        self._adapter, self._config, self._task, self._home = None, None, None, None
        self._last_state = None
        self._sequence = 0

    def _rpc_vehicle_id(self) -> str:
        """Map a nonempty platform vehicle ID to legacy AirSim's default name."""
        assert self._config is not None
        value = self._options.get("rpc_vehicle_id", "")
        if not isinstance(value, str):
            raise PlatformContractException(ContractError("invalid_argument", "connection.rpc_vehicle_id must be a string"))
        return value
    def _state(self):
        assert self._adapter is not None and self._config is not None
        state = self._adapter.state(self._rpc_vehicle_id())
        self._last_state = state
        return state

    def _wait_for(self, predicate, timeout_s: float, message: str):
        deadline = time.monotonic() + timeout_s
        poll = min(_number(self._options, "state_poll_interval_s", 0.2), timeout_s)
        while True:
            state = self._state()
            if predicate(state):
                return state
            if time.monotonic() >= deadline:
                raise PlatformContractException(ContractError("timeout", message))
            time.sleep(min(poll, max(0.0, deadline - time.monotonic())))

    def _near_ground_stationary(self, state) -> bool:
        assert self._home is not None
        return (
            abs(state.pose_ned.down_m - self._home.down_m) <=
            _number(self._options, "near_ground_tolerance_m", 0.5)
            and math.hypot(*state.velocity_ned_mps) <=
            _number(self._options, "near_ground_speed_tolerance_mps", 0.2)
        )

    def _confirm_command_by_state(self, result, operation: str, rpc_method: str,
                                  predicate, timeout_s: float, message: str) -> None:
        legacy_false = (
            result.completed and not result.accepted and result.error is not None
            and result.error.code is ErrorCode.SIMULATOR
            and result.error.message == f"{rpc_method} returned false"
        )
        if not legacy_false:
            self._check(result, operation)
        try:
            self._wait_for(predicate, timeout_s, message)
        except PlatformContractException as exc:
            if legacy_false:
                raise PlatformContractException(ContractError(
                    "simulator", f"{operation}: {rpc_method} returned false; {message}"
                )) from exc
            raise
        if legacy_false:
            self.runtime_metadata.setdefault("state_confirmed_rpc_false", []).append(operation)

    @staticmethod
    def _distance(pose, position: PositionNed) -> float:
        return math.sqrt((pose.north_m - position.north_m) ** 2 +
                         (pose.east_m - position.east_m) ** 2 +
                         (pose.down_m - position.down_m) ** 2)

    @staticmethod
    def _check(result, operation: str) -> None:
        if not result.accepted or not result.completed or result.error is not None:
            error = _error(result, "simulator")
            raise PlatformContractException(ContractError(error.code, f"{operation}: {error.message}", error.retryable))
