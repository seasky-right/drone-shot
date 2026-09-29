"""AirSim RPC adapter for the frozen UE4 baseline.

This is the only module allowed to know AirSim's RPC names, NED response maps,
or legacy landed-state values. It deliberately does not start the UE process.
"""

from __future__ import annotations

import socket
import struct
import time
import uuid
from typing import Any, Callable, Mapping

from .contracts import (
    SCHEMA_VERSION,
    Capability,
    CommandResult,
    ContractError,
    ErrorCode,
    Observation,
    ObservationMetadata,
    PoseNed,
    SensorKind,
    SensorDescriptor,
    SensorRequest,
    SimulatorContractException,
    VehicleState,
)


class AirSimAdapterError(SimulatorContractException):
    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.SIMULATOR,
        retryable: bool = False,
        details: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(ContractError(code, message, retryable, details or {}))


class AirSimLegacyAdapter:
    """Adapter for the packaged UE4 AirSim environment only.

    ``rpc_factory`` receives host and port and returns an object exposing
    ``call(method, *params, timeout=...)`` and ``close()``. Keeping the
    transport injectable permits contract tests without AirSim or msgpack.
    """

    simulator_id = "legacy-ue4-airsim"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 41451,
        rpc_factory: Callable[[str, int], Any] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._rpc_factory = rpc_factory or _msgpack_rpc_factory
        self._rpc: Any | None = None
        self._confirmed_sensors: dict[str, set[SensorDescriptor]] = {}

    def connect(self, timeout_s: float = 5.0) -> CommandResult:
        try:
            self._rpc = self._rpc_factory(self._host, self._port)
            self._call("ping", timeout_s=timeout_s)
            return self._result("connect", True, True)
        except (OSError, AirSimAdapterError) as error:
            self.close()
            return self._failure_from_exception("connect", error, ErrorCode.TRANSPORT, True)

    def close(self) -> None:
        if self._rpc is not None:
            self._rpc.close()
            self._rpc = None

    @property
    def declared_capabilities(self) -> frozenset[Capability]:
        """Features the adapter can translate; resources still need discovery."""
        return frozenset(
            {
                Capability.API_CONTROL,
                Capability.ARM_DISARM,
                Capability.TAKEOFF,
                Capability.LAND,
                Capability.HOVER,
                Capability.VELOCITY_BODY,
                Capability.STATE,
                Capability.RGB_CAMERA,
                Capability.DEPTH_CAMERA,
            }
        )

    def capabilities(self, vehicle_id: str) -> frozenset[Capability]:
        """Operation types that this adapter can translate for a vehicle."""
        del vehicle_id
        return self.declared_capabilities

    def sensors(self, vehicle_id: str) -> tuple[SensorDescriptor, ...]:
        return tuple(sorted(self._confirmed_sensors.get(vehicle_id, set()), key=lambda item: (item.sensor_id, item.kind.value)))

    def state(self, vehicle_id: str) -> VehicleState:
        state = self._call("getMultirotorState", vehicle_id)
        if not isinstance(state, Mapping):
            raise AirSimAdapterError("getMultirotorState returned an invalid payload")
        kinematics = _mapping(state, "kinematics_estimated")
        position = _mapping(kinematics, "position")
        orientation = _mapping(kinematics, "orientation")
        velocity = _mapping(kinematics, "linear_velocity")
        return VehicleState(
            schema=SCHEMA_VERSION,
            simulator_time_ns=_optional_int(state.get("timestamp")),
            wall_time_ns=time.time_ns(),
            vehicle_id=vehicle_id,
            pose_ned=PoseNed(
                north_m=_float(position.get("x_val")),
                east_m=_float(position.get("y_val")),
                down_m=_float(position.get("z_val")),
                orientation_w=_float(orientation.get("w_val"), 1.0),
                orientation_x=_float(orientation.get("x_val")),
                orientation_y=_float(orientation.get("y_val")),
                orientation_z=_float(orientation.get("z_val")),
            ),
            velocity_ned_mps=(
                _float(velocity.get("x_val")),
                _float(velocity.get("y_val")),
                _float(velocity.get("z_val")),
            ),
            armed=None,
            api_control=None,
            landed=(None if state.get("landed_state") is None else _optional_int(state.get("landed_state")) == 0),
        )

    def plot_goal(self, position: PoseNed) -> None:
        point = {"x_val": position.north_m, "y_val": position.east_m,
                 "z_val": position.down_m}
        response = self._call("simPlotPoints", [point], [0.0, 1.0, 0.1, 1.0],
                              20.0, 120.0, False)
        if response is False:
            raise AirSimAdapterError("simPlotPoints returned false")

    def collision_info(self, vehicle_id: str) -> Mapping[str, object]:
        info = self._call("simGetCollisionInfo", vehicle_id)
        if not isinstance(info, Mapping) or not isinstance(info.get("has_collided"), bool):
            raise AirSimAdapterError("simGetCollisionInfo returned an invalid payload")
        return info

    def reset_vehicle(self) -> CommandResult:
        """AirSim reset is simulator-wide and accepts no vehicle argument."""
        try:
            response = self._call("reset")
            if response is False:
                return self._failure("reset", ErrorCode.SIMULATOR, "AirSim reset returned false")
            self._confirmed_sensors.clear()
            return self._result("reset", True, True)
        except (OSError, AirSimAdapterError) as error:
            return self._failure_from_exception("reset", error, ErrorCode.TRANSPORT, True)

    def set_vehicle_pose(self, vehicle_id: str, pose: PoseNed) -> CommandResult:
        """Set a measured staging pose; the caller must confirm the resulting state."""
        return self._command(
            "simSetVehiclePose", vehicle_id,
            {
                "position": {"x_val": pose.north_m, "y_val": pose.east_m, "z_val": pose.down_m},
                "orientation": {
                    "w_val": pose.orientation_w, "x_val": pose.orientation_x,
                    "y_val": pose.orientation_y, "z_val": pose.orientation_z,
                },
            }, False,
        )

    def move_to_z(self, vehicle_id: str, down_m: float, speed_mps: float, timeout_s: float) -> CommandResult:
        """Legacy near-ground landing fallback; caller verifies the final state."""
        return self._command(
            "moveToZ", vehicle_id, down_m, speed_mps, timeout_s,
            {"is_rate": True, "yaw_or_rate": 0.0}, -1.0, 1.0,
            timeout_s=timeout_s + 5.0,
        )
    def set_api_control(self, vehicle_id: str, enabled: bool) -> CommandResult:
        return self._command("enableApiControl", vehicle_id, enabled)

    def arm(self, vehicle_id: str, armed: bool) -> CommandResult:
        return self._command("armDisarm", vehicle_id, armed)

    def takeoff(self, vehicle_id: str, timeout_s: float) -> CommandResult:
        return self._command("takeoff", vehicle_id, timeout_s, timeout_s=timeout_s)

    def land(self, vehicle_id: str, timeout_s: float) -> CommandResult:
        return self._command("land", vehicle_id, timeout_s, timeout_s=timeout_s)

    def hover(self, vehicle_id: str) -> CommandResult:
        return self._command("hover", vehicle_id)

    def move_body_velocity(
        self,
        vehicle_id: str,
        forward_mps: float,
        right_mps: float,
        down_mps: float,
        duration_s: float,
        rpc_timeout_s: float | None = None,
    ) -> CommandResult:
        yaw_mode = {"is_rate": True, "yaw_or_rate": 0.0}
        return self._command(
            "moveByVelocityBodyFrame",
            vehicle_id,
            forward_mps,
            right_mps,
            down_mps,
            duration_s,
            0,
            yaw_mode,
            timeout_s=rpc_timeout_s if rpc_timeout_s is not None else max(duration_s + 2.0, 3.0),
        )

    def observe(self, vehicle_id: str, request: SensorRequest) -> Observation:
        image_type = {SensorKind.RGB: 0, SensorKind.DEPTH: 1}.get(request.kind)
        if image_type is None:
            raise AirSimAdapterError(f"Unsupported AirSim sensor kind: {request.kind}")
        response = self._call(
            "simGetImages",
            [{"camera_name": request.sensor_id, "image_type": image_type,
              "pixels_as_float": request.kind is SensorKind.DEPTH,
              "compress": request.kind is SensorKind.RGB}],
            vehicle_id,
        )
        if not isinstance(response, list) or not response or not isinstance(response[0], Mapping):
            raise AirSimAdapterError("simGetImages returned no image response")
        image = response[0]
        payload = image.get("image_data_uint8") if request.kind is SensorKind.RGB else image.get("image_data_float")
        if isinstance(payload, list):
            payload = struct.pack(f"<{len(payload)}f", *(float(item) for item in payload))
        if payload is not None and not isinstance(payload, bytes):
            raise AirSimAdapterError("AirSim image payload is not binary")
        frame_id = f"airsim/{vehicle_id or 'default'}/camera/{request.sensor_id}/optical"
        descriptor = SensorDescriptor(request.sensor_id, request.kind, frame_id)
        self._confirmed_sensors.setdefault(vehicle_id, set()).add(descriptor)
        return Observation(
            metadata=ObservationMetadata(
                schema=SCHEMA_VERSION,
                simulator_time_ns=_optional_int(image.get("time_stamp")),
                wall_time_ns=time.time_ns(),
                vehicle_id=vehicle_id,
                sensor_id=request.sensor_id,
                kind=request.kind,
                frame_id=frame_id,
                encoding="png" if request.kind is SensorKind.RGB else "airsim-float32",
                width_px=_int_or_none(image.get("width")),
                height_px=_int_or_none(image.get("height")),
                pose_ned=_camera_pose_ned(image),
                attributes={"pose_reference_frame": "ned"},
            ),
            payload=payload if request.include_payload else None,
        )

    def _command(
        self, method: str, vehicle_id: str, *args: object, timeout_s: float = 5.0
    ) -> CommandResult:
        try:
            response = self._call(method, *args, vehicle_id, timeout_s=timeout_s)
            if response is False:
                return self._failure(method, ErrorCode.SIMULATOR, f"{method} returned false")
            return self._result(method, True, True)
        except (OSError, AirSimAdapterError) as error:
            return self._failure_from_exception(method, error, ErrorCode.TRANSPORT, True)

    def _call(self, method: str, *params: object, timeout_s: float = 5.0) -> object:
        if self._rpc is None:
            raise AirSimAdapterError("Adapter is not connected", ErrorCode.NOT_CONNECTED)
        return self._rpc.call(method, *params, timeout=timeout_s)

    @staticmethod
    def _result(operation: str, accepted: bool, completed: bool) -> CommandResult:
        return CommandResult(
            SCHEMA_VERSION,
            str(uuid.uuid4()),
            operation,
            accepted,
            completed,
            None,
            time.time_ns(),
        )

    @staticmethod
    def _failure(
        operation: str, code: ErrorCode, message: str, retryable: bool = False
    ) -> CommandResult:
        return CommandResult(
            SCHEMA_VERSION,
            str(uuid.uuid4()),
            operation,
            False,
            True,
            None,
            time.time_ns(),
            ContractError(code, message, retryable),
        )

    @classmethod
    def _failure_from_exception(
        cls,
        command_id: str,
        error: OSError | AirSimAdapterError,
        fallback_code: ErrorCode,
        fallback_retryable: bool,
    ) -> CommandResult:
        if isinstance(error, AirSimAdapterError):
            return cls._failure(command_id, error.error.code, str(error), error.error.retryable)
        return cls._failure(command_id, fallback_code, str(error), fallback_retryable)


def _msgpack_rpc_factory(host: str, port: int) -> Any:
    # The package remains optional until the legacy adapter is actually selected.
    import msgpack  # type: ignore[import-not-found]

    return _MsgpackRpcClient(host, port, msgpack)


class _MsgpackRpcClient:
    def __init__(self, host: str, port: int, msgpack: Any) -> None:
        self._socket = socket.create_connection((host, port), timeout=2.0)
        self._unpacker = msgpack.Unpacker(raw=False, strict_map_key=False)
        self._msgpack = msgpack
        self._message_id = 0

    def close(self) -> None:
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._socket.close()

    def call(self, method: str, *params: object, timeout: float = 5.0) -> object:
        self._message_id += 1
        message_id = self._message_id
        self._socket.settimeout(timeout)
        self._socket.sendall(
            self._msgpack.packb([0, message_id, method, list(params)], use_bin_type=True)
        )
        while True:
            for response in self._unpacker:
                if not isinstance(response, list) or len(response) != 4:
                    continue
                message_type, response_id, error, result = response
                if message_type == 1 and response_id == message_id:
                    if error not in (None, False, "", b""):
                        raise AirSimAdapterError(f"{method} failed: {error}")
                    return result
            data = self._socket.recv(65536)
            if not data:
                raise AirSimAdapterError("AirSim RPC connection closed", ErrorCode.TRANSPORT, True)
            self._unpacker.feed(data)


def _mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise AirSimAdapterError(f"AirSim response is missing {key}")
    return item


def _float(value: object, default: float = 0.0) -> float:
    return float(value) if value is not None else default


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None


def _int_or_none(value: object) -> int | None:
    return int(value) if value is not None else None


def _camera_pose_ned(image: Mapping[str, object]) -> PoseNed | None:
    position = image.get("camera_position")
    orientation = image.get("camera_orientation")
    if not isinstance(position, Mapping) or not isinstance(orientation, Mapping):
        return None
    return PoseNed(
        north_m=_float(position.get("x_val")),
        east_m=_float(position.get("y_val")),
        down_m=_float(position.get("z_val")),
        orientation_w=_float(orientation.get("w_val"), 1.0),
        orientation_x=_float(orientation.get("x_val")),
        orientation_y=_float(orientation.get("y_val")),
        orientation_z=_float(orientation.get("z_val")),
    )
