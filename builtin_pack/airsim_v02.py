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
        task = TaskSpec("airsim-v02", "reach_point", 3600, home,
                        "relative_to_home", seed=scenario.seed)
        config = BackendConfig("airsim", vehicle, self._config.get("connection", {}),
                               self._temporary.name)
        try:
            observation = self._backend.reset(config, task)
            self._vehicle_id = vehicle
            self._active = True
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
        self.spec = ScenarioSpecV02(
            SCENARIO_ID, "1.0.0", config.get("checksum", "legacy-ue4-fixed-scene"),
            {}, None, {vehicle: home}, "none",
        )
        self.truth = {}

    def close(self) -> None:
        pass


class MoveToAgent:
    def __init__(self, config: Mapping[str, object]) -> None:
        self.target = dict(config["target"])
        self.deadline_s = float(config.get("deadline_s", 30))

    def act(self, observation: PlatformObservationV02) -> ActionV02:
        return ActionV02(
            f"move-{observation.sequence}", observation.vehicle_id, MOVE_KIND,
            ActionChannel.CONTROL, MOVE_SCHEMA, self.target, self.deadline_s,
        )

    def close(self) -> None:
        pass


class ReachPointTask:
    def __init__(self, config: Mapping[str, object]) -> None:
        self.target = _position(config["target"])
        self.tolerance = float(config.get("tolerance_m", 0.75))

    def complete(self, snapshot: EpisodeSnapshotV02,
                 truth: Mapping[str, object]) -> bool:
        observation = next(iter(snapshot.observations.values()))
        position = _position(observation.state)
        return math.dist((position.north_m, position.east_m, position.down_m),
                         (self.target.north_m, self.target.east_m, self.target.down_m)) <= self.tolerance

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
