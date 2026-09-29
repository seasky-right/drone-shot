"""Single-vehicle v0.2 AirSim components backed by the proven v0.1 adapter."""

from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import Mapping

from backends.airsim import AirSimBackend
from contracts import Action, ActionKind, BackendConfig, PositionNed, TaskSpec
from contracts.data_v02 import (
    ActionChannel, ActionV02, CapabilitySetV02, EpisodeSnapshotV02,
    PlatformObservationV02, ScenarioSpecV02, SensorReferenceV02,
)
from core.multi_vehicle import ActionOutcome


SCENARIO_ID = "drone.legacy-ue4"
MOVE_KIND = "drone/move-to"
MOVE_SCHEMA = "drone.move-to/v1"
HOVER_KIND = "drone/hover"
HOVER_SCHEMA = "drone.hover/v1"
SENSOR_KINDS = {"rgb": "drone/rgb", "depth": "drone/depth"}
BENCHMARK_VERSION = "airsim-reach-point/v0.1-fixed-scene"


def _position(value: Mapping[str, object]) -> PositionNed:
    return PositionNed.from_dict({key: value[key] for key in
                                  ("north_m", "east_m", "down_m")})


class AirSimBackendV02:
    def __init__(self, config: Mapping[str, object], context, *, backend_factory=None) -> None:
        self._config = dict(config)
        self._context = context
        self._backend = (backend_factory or AirSimBackend)()
        self._temporary = tempfile.TemporaryDirectory(prefix="drone-airsim-v02-")
        self._vehicle_id: str | None = None
        self._active = False
        self._closed = False
        self._copied: set[str] = set()
        self._confirmed_resources: dict[str, str] = {}
        self._target: dict[str, float] | None = None

    def capabilities(self) -> CapabilitySetV02:
        resources = self._confirmed_resources
        requested = self._config.get("connection", {}).get("sensors", [])
        types = tuple(sorted({SENSOR_KINDS[item["kind"]] for item in requested}))
        return CapabilitySetV02(
            (MOVE_KIND, HOVER_KIND), types, resources,
            "local_ned", ("wall", "simulator"), 1, ("load", "reset"), False, True,
        )

    def reset(self, scenario: ScenarioSpecV02,
              vehicle_ids: tuple[str, ...]) -> EpisodeSnapshotV02:
        if self._closed or self._active:
            raise RuntimeError("AirSim episode cannot be reset twice")
        if len(vehicle_ids) != 1:
            raise ValueError("AirSim v0.2 backend supports one vehicle")
        if scenario.scenario_id != self._config.get("scenario_id", SCENARIO_ID):
            raise ValueError("AirSim scenario ID does not match the configured scene")
        if scenario.resources:
            raise ValueError("AirSim v0.2 backend cannot load scenario resources")
        vehicle = vehicle_ids[0]
        initial = scenario.initial_states.get(vehicle)
        if not isinstance(initial, dict):
            raise ValueError(f"missing initial state for {vehicle}")
        home = _position(initial)
        if "target" not in initial:
            raise ValueError("AirSim scenario requires a target")
        target = _position(initial["target"])
        task = TaskSpec("airsim-v02", "reach_point", 3600, home,
                        "relative_to_home", seed=scenario.seed)
        connection = dict(self._config.get("connection", {}))
        connection.setdefault("abort_on_collision", True)
        config = BackendConfig("airsim", vehicle, connection,
                               self._temporary.name)
        try:
            observation = self._backend.reset(config, task)
            self._vehicle_id = vehicle
            self._active = True
            self._backend.plot_goal(target)
            self._target = target.to_dict()
            if observation.missing_sensors:
                raise RuntimeError(f"AirSim sensor unavailable: {observation.missing_sensors}")
            self._confirmed_resources = {
                f"{sensor.kind}:{sensor.sensor_id}": SENSOR_KINDS[sensor.kind]
                for sensor in observation.sensors
            }
            return self._snapshot(observation)
        except BaseException:
            self.close()
            raise

    def execute(self, action: ActionV02) -> ActionOutcome:
        if not self._active or action.vehicle_id != self._vehicle_id:
            return ActionOutcome(action.action_id, action.vehicle_id, False, "unknown vehicle")
        if action.channel is not ActionChannel.CONTROL:
            return ActionOutcome(action.action_id, action.vehicle_id, False, "unsupported channel")
        try:
            if action.kind == MOVE_KIND and action.payload_schema == MOVE_SCHEMA:
                if set(action.payload) != {"north_m", "east_m", "down_m"}:
                    raise ValueError("move-to requires north_m, east_m, down_m")
                target = _position(action.payload)
                legacy = Action(action.action_id, ActionKind.MOVE_TO, action.vehicle_id,
                                action.deadline_s, target)
            elif action.kind == HOVER_KIND and action.payload_schema == HOVER_SCHEMA:
                if action.payload:
                    raise ValueError("hover payload must be empty")
                legacy = Action(action.action_id, ActionKind.HOVER, action.vehicle_id,
                                action.deadline_s)
            else:
                raise ValueError("unsupported AirSim action or payload schema")
            result = self._backend.execute(legacy)
            return ActionOutcome(action.action_id, action.vehicle_id, result.succeeded,
                                 None if result.succeeded else
                                 f"{result.error.code}: {result.error.message}")
        except (ValueError, TypeError, KeyError) as exc:
            return ActionOutcome(action.action_id, action.vehicle_id, False, str(exc))

    def observe(self) -> EpisodeSnapshotV02:
        if not self._active:
            raise RuntimeError("AirSim episode is not active")
        return self._snapshot(self._backend.observe())

    def _snapshot(self, observation) -> EpisodeSnapshotV02:
        sensors = []
        for sensor in observation.sensors:
            if sensor.relative_path not in self._copied:
                content = (Path(self._temporary.name) / sensor.relative_path).read_bytes()
                self._context.artifacts.write_bytes(sensor.relative_path, content)
                self._copied.add(sensor.relative_path)
            sensors.append(SensorReferenceV02(sensor.sensor_id, SENSOR_KINDS[sensor.kind],
                                              sensor.relative_path, sensor.captured_wall_time_ns))
        position = observation.position_ned
        state = {"north_m": position.north_m, "east_m": position.east_m,
                 "down_m": position.down_m,
                 "velocity_ned_mps": list(observation.velocity_ned_mps)}
        if self._target is not None:
            state["target"] = self._target
        try:
            state["collision"] = self._backend.collision_info()
        except Exception as exc:
            state["collision"] = {"observed": False, "error": str(exc)}
        platform = PlatformObservationV02(
            observation.sequence, observation.vehicle_id, observation.wall_time_ns, state,
            tuple(sensors), observation.missing_sensors,
            simulator_time_ns=observation.simulator_time_ns,
        )
        return EpisodeSnapshotV02(observation.sequence, {observation.vehicle_id: platform})

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        error = None
        try:
            if self._active:
                cleanup = self._backend.cleanup()
                if not cleanup.succeeded:
                    error = RuntimeError(f"AirSim cleanup failed: {cleanup.error}")
        finally:
            try:
                self._backend.close()
            finally:
                self._temporary.cleanup()
                self._active = False
        if error is not None:
            raise error


class ReachPointScenario:
    def __init__(self, config: Mapping[str, object]) -> None:
        home = config.get("home", {"north_m": 0, "east_m": 0, "down_m": 0})
        vehicle = config.get("vehicle_id", "drone-1")
        target = _position(config["target"]).to_dict()
        self.spec = ScenarioSpecV02(
            SCENARIO_ID, "1.0.0", config.get("checksum", "legacy-ue4-fixed-scene"),
            {}, None, {vehicle: {**home, "target": target}}, "none",
        )
        self.truth = {}

    def close(self) -> None:
        pass


class MoveToAgent:
    def __init__(self, config: Mapping[str, object]) -> None:
        self.target = (_position(config["target"]).to_dict()
                       if "target" in config else None)
        self.deadline_s = float(config.get("deadline_s", 30))

    def act(self, observation: PlatformObservationV02) -> ActionV02:
        target = _position(observation.state["target"]).to_dict() if "target" in observation.state else self.target
        if target is None:
            raise ValueError("AirSim observation has no target")
        if self.target is not None and self.target != target:
            raise ValueError("agent target differs from scenario target")
        return ActionV02(
            f"move-{observation.sequence}", observation.vehicle_id, MOVE_KIND,
            ActionChannel.CONTROL, MOVE_SCHEMA, target, self.deadline_s,
        )

    def close(self) -> None:
        pass


class ReachPointTask:
    def __init__(self, config: Mapping[str, object]) -> None:
        self.target = _position(config["target"]) if "target" in config else None
        self.tolerance = float(config.get("tolerance_m", 0.75))

    def complete(self, snapshot: EpisodeSnapshotV02,
                 truth: Mapping[str, object]) -> bool:
        observation = next(iter(snapshot.observations.values()))
        position = _position(observation.state)
        target = (_position(observation.state["target"]) if "target" in observation.state
                  else self.target)
        if target is None:
            raise ValueError("AirSim observation has no target")
        if self.target is not None and self.target != target:
            raise ValueError("task target differs from scenario target")
        return math.dist((position.north_m, position.east_m, position.down_m),
                         (target.north_m, target.east_m, target.down_m)) <= self.tolerance

    def close(self) -> None:
        pass


class ReachPointEvaluator:
    def evaluate(self, result: Mapping[str, object]) -> dict[str, float]:
        trajectory = result["trajectory"]
        final = next(iter(result["final_snapshot"]["observations"].values()))
        observations = ([next(iter(step["before"]["observations"].values()))
                         for step in trajectory] + [final])
        target = _position(final["state"]["target"])
        points = [_position(item["state"]) for item in observations]
        xyz = lambda point: (point.north_m, point.east_m, point.down_m)
        collision = [item["state"].get("collision", {}) for item in observations]
        sampled = [item["has_collided"] for item in collision
                   if isinstance(item, Mapping) and isinstance(item.get("has_collided"), bool)]
        complete = len(sampled) == len(observations)
        sampled_safe_success = (result["success"] is True and complete
                                and not any(sampled) and not result["cleanup_errors"])
        elapsed = max(0.0, (observations[-1]["wall_time_ns"] -
                            observations[0]["wall_time_ns"]) / 1e9)
        return {
            "airsim/reach_success": float(result["success"] is True),
            "airsim/sampled_safe_success": float(sampled_safe_success),
            "airsim/elapsed_wall_s": elapsed,
            "airsim/final_error_m": math.dist(xyz(points[-1]), xyz(target)),
            "airsim/observed_path_m": sum(math.dist(xyz(a), xyz(b))
                                           for a, b in zip(points, points[1:])),
            "airsim/collision_sample_count": float(len(sampled)),
            "airsim/collision_positive_sample_count": float(sum(sampled)),
            "airsim/collision_observation_complete": float(complete),
            "airsim/cleanup_error_count": float(len(result["cleanup_errors"])),
        }

    def close(self) -> None:
        pass


class ReachPointBenchmark:
    def cases(self):
        return tuple({"case_id": f"{BENCHMARK_VERSION}.repeat-{index}"}
                     for index in (1, 2))

    def close(self) -> None:
        pass


class ReachPointProcessor:
    def process(self, read_result, episode_ids):
        results = [read_result(episode_id) for episode_id in episode_ids]
        count = len(results)
        successes = sum(item["success"] is True for item in results)
        safe_successes = sum(item.get("metrics", {}).get(
            "airsim/sampled_safe_success", 0) == 1 for item in results)
        positive = sum(item.get("metrics", {}).get(
            "airsim/collision_positive_sample_count", 0) > 0 for item in results)
        complete = sum(item.get("metrics", {}).get(
            "airsim/collision_observation_complete", 0) == 1 for item in results)
        return {
            "version": BENCHMARK_VERSION,
            "fixed_scene_repeats": count,
            "success_count": successes,
            "failure_count": count - successes,
            "success_rate_all_attempts": successes / count if count else 0.0,
            "sampled_safe_success_count": safe_successes,
            "sampled_safe_success_rate_all_attempts": safe_successes / count if count else 0.0,
            "observed_collision_episode_count": positive,
            "complete_collision_sampling_episode_count": complete,
            "cleanup_error_episode_count": sum(bool(item.get("cleanup_errors")) for item in results),
        }

    def close(self) -> None:
        pass


def create_backend(*, config, context):
    return AirSimBackendV02(config, context)


def create_scenario(*, config, context):
    return ReachPointScenario(config)


def create_agent(*, config, context):
    return MoveToAgent(config)


def create_task(*, config, context):
    return ReachPointTask(config)


def create_evaluator(*, config, context):
    return ReachPointEvaluator()


def create_benchmark(*, config, context):
    return ReachPointBenchmark()


def create_processor(*, config, context):
    return ReachPointProcessor()
