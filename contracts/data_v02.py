"""Candidate JSON-safe platform data schema v0.2, separate from sensor RPC data."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from .model import ContractValidationError


PLATFORM_SCHEMA_V02 = "drone.platform.contract/v0.2"
_NAME = re.compile(r"^[a-z][a-z0-9_.-]*/[a-z][a-z0-9_.-]*$")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractValidationError(f"{name} must be a non-negative integer")
    return value


def _number(value: object, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractValidationError(f"{name} must be finite")
    if positive and value <= 0:
        raise ContractValidationError(f"{name} must be positive")
    return float(value)


def _json(value: object, name: str) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ContractValidationError(f"{name} must be JSON-safe")
        return value
    if isinstance(value, (list, tuple)):
        return [_json(item, name) for item in value]
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ContractValidationError(f"{name} keys must be strings")
        return {key: _json(item, name) for key, item in value.items()}
    raise ContractValidationError(f"{name} must be JSON-safe")


def _object(value: object, name: str) -> dict[str, object]:
    checked = _json(value, name)
    if not isinstance(checked, dict):
        raise ContractValidationError(f"{name} must be an object")
    return checked


def _fields(data: Mapping[str, object], allowed: set[str], required: set[str], name: str) -> None:
    unknown, missing = set(data) - allowed, required - set(data)
    if unknown or missing:
        raise ContractValidationError(f"{name} fields invalid: unknown={sorted(unknown)}, missing={sorted(missing)}")


def _namespaced(value: object, name: str) -> str:
    text = _text(value, name)
    if not _NAME.fullmatch(text):
        raise ContractValidationError(f"{name} must be namespace/name")
    return text


def _schema(value: object) -> None:
    if value != PLATFORM_SCHEMA_V02:
        raise ContractValidationError("unsupported platform schema; explicit migration required")


def _path(value: object, name: str) -> str:
    path = _text(value, name)
    if "\\" in path or ":" in path or path.startswith("/") or any(p in ("", ".", "..") for p in path.split("/")):
        raise ContractValidationError(f"{name} must be an episode-relative POSIX path")
    return path


class ActionChannel(str, Enum):
    CONTROL = "control"
    SAMPLING = "sampling"
    REPORT = "report"


@dataclass(frozen=True)
class ActionV02:
    action_id: str
    vehicle_id: str
    kind: str
    channel: ActionChannel
    payload_schema: str
    payload: Mapping[str, object]
    deadline_s: float
    correlation_id: str | None = None
    schema: str = PLATFORM_SCHEMA_V02

    def __post_init__(self) -> None:
        _schema(self.schema)
        _text(self.action_id, "action_id"); _text(self.vehicle_id, "vehicle_id")
        _namespaced(self.kind, "kind"); _text(self.payload_schema, "payload_schema")
        if not isinstance(self.channel, ActionChannel):
            raise ContractValidationError("channel must be an ActionChannel")
        object.__setattr__(self, "payload", _object(self.payload, "payload"))
        object.__setattr__(self, "deadline_s", _number(self.deadline_s, "deadline_s", positive=True))
        if self.correlation_id is not None:
            _text(self.correlation_id, "correlation_id")

    def to_dict(self) -> dict[str, object]:
        return dict(schema=self.schema, action_id=self.action_id, vehicle_id=self.vehicle_id,
                    kind=self.kind, channel=self.channel.value, payload_schema=self.payload_schema,
                    payload=self.payload, deadline_s=self.deadline_s, correlation_id=self.correlation_id)

    @classmethod
    def from_dict(cls, value: object) -> ActionV02:
        data = _object(value, "action")
        fields = {"schema", "action_id", "vehicle_id", "kind", "channel", "payload_schema", "payload", "deadline_s", "correlation_id"}
        _fields(data, fields, fields - {"correlation_id"}, "action")
        try:
            channel = ActionChannel(data["channel"])
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("unknown action channel") from exc
        return cls(data["action_id"], data["vehicle_id"], data["kind"], channel,
                   data["payload_schema"], data["payload"], data["deadline_s"], data.get("correlation_id"), data["schema"])


@dataclass(frozen=True)
class SensorReferenceV02:
    sensor_id: str
    kind: str
    relative_path: str
    captured_wall_time_ns: int

    def __post_init__(self) -> None:
        _text(self.sensor_id, "sensor_id"); _namespaced(self.kind, "sensor kind")
        _path(self.relative_path, "relative_path"); _integer(self.captured_wall_time_ns, "captured_wall_time_ns")

    def to_dict(self) -> dict[str, object]:
        return dict(sensor_id=self.sensor_id, kind=self.kind, relative_path=self.relative_path,
                    captured_wall_time_ns=self.captured_wall_time_ns)

    @classmethod
    def from_dict(cls, value: object) -> SensorReferenceV02:
        data = _object(value, "sensor reference")
        fields = {"sensor_id", "kind", "relative_path", "captured_wall_time_ns"}
        _fields(data, fields, fields, "sensor reference")
        return cls(**data)


@dataclass(frozen=True)
class PlatformObservationV02:
    sequence: int
    vehicle_id: str
    wall_time_ns: int
    state: Mapping[str, object]
    sensors: tuple[SensorReferenceV02, ...] = ()
    missing_sensors: Mapping[str, str] = field(default_factory=dict)
    extensions: Mapping[str, object] = field(default_factory=dict)
    simulator_time_ns: int | None = None
    schema: str = PLATFORM_SCHEMA_V02

    def __post_init__(self) -> None:
        _schema(self.schema)
        _integer(self.sequence, "sequence"); _text(self.vehicle_id, "vehicle_id")
        _integer(self.wall_time_ns, "wall_time_ns")
        if self.simulator_time_ns is not None:
            _integer(self.simulator_time_ns, "simulator_time_ns")
        object.__setattr__(self, "state", _object(self.state, "state"))
        if not all(isinstance(s, SensorReferenceV02) for s in self.sensors):
            raise ContractValidationError("sensors must contain SensorReferenceV02")
        object.__setattr__(self, "sensors", tuple(self.sensors))
        missing = _object(self.missing_sensors, "missing_sensors")
        if not all(isinstance(v, str) and v for v in missing.values()):
            raise ContractValidationError("missing_sensors values must be non-empty reasons")
        object.__setattr__(self, "missing_sensors", missing)
        extensions = _object(self.extensions, "extensions")
        for key in extensions:
            _namespaced(key, "extension key")
        object.__setattr__(self, "extensions", extensions)

    def to_dict(self) -> dict[str, object]:
        return dict(schema=self.schema, sequence=self.sequence, vehicle_id=self.vehicle_id,
                    wall_time_ns=self.wall_time_ns, simulator_time_ns=self.simulator_time_ns,
                    state=self.state, sensors=[s.to_dict() for s in self.sensors],
                    missing_sensors=self.missing_sensors, extensions=self.extensions)

    @classmethod
    def from_dict(cls, value: object) -> PlatformObservationV02:
        data = _object(value, "observation")
        fields = {"schema", "sequence", "vehicle_id", "wall_time_ns", "simulator_time_ns", "state", "sensors", "missing_sensors", "extensions"}
        _fields(data, fields, {"schema", "sequence", "vehicle_id", "wall_time_ns", "state"}, "observation")
        sensors = data.get("sensors", [])
        if not isinstance(sensors, list):
            raise ContractValidationError("sensors must be an array")
        return cls(data["sequence"], data["vehicle_id"], data["wall_time_ns"], data["state"],
                   tuple(SensorReferenceV02.from_dict(s) for s in sensors), data.get("missing_sensors", {}),
                   data.get("extensions", {}), data.get("simulator_time_ns"), data["schema"])


@dataclass(frozen=True)
class EpisodeSnapshotV02:
    """A simultaneous logical snapshot keyed by stable vehicle IDs."""

    sequence: int
    observations: Mapping[str, PlatformObservationV02]
    missing_vehicles: Mapping[str, str] = field(default_factory=dict)
    schema: str = PLATFORM_SCHEMA_V02

    def __post_init__(self) -> None:
        _schema(self.schema); _integer(self.sequence, "sequence")
        if not isinstance(self.observations, Mapping):
            raise ContractValidationError("observations must be a vehicle map")
        for vehicle_id, observation in self.observations.items():
            _text(vehicle_id, "vehicle_id")
            if not isinstance(observation, PlatformObservationV02) or observation.vehicle_id != vehicle_id:
                raise ContractValidationError("observation vehicle ID mismatch")
        missing = _object(self.missing_vehicles, "missing_vehicles")
        if set(missing) & set(self.observations) or not all(isinstance(v, str) and v for v in missing.values()):
            raise ContractValidationError("missing_vehicles must have distinct IDs and reasons")
        if not self.observations and not missing:
            raise ContractValidationError("snapshot must contain an observation or missing vehicle")
        object.__setattr__(self, "observations", dict(self.observations))
        object.__setattr__(self, "missing_vehicles", missing)

    def to_dict(self) -> dict[str, object]:
        return dict(schema=self.schema, sequence=self.sequence,
                    observations={key: value.to_dict() for key, value in self.observations.items()},
                    missing_vehicles=self.missing_vehicles)

    @classmethod
    def from_dict(cls, value: object) -> EpisodeSnapshotV02:
        data = _object(value, "snapshot")
        fields = {"schema", "sequence", "observations", "missing_vehicles"}
        _fields(data, fields, fields - {"missing_vehicles"}, "snapshot")
        observations = _object(data["observations"], "observations")
        return cls(data["sequence"], {key: PlatformObservationV02.from_dict(item) for key, item in observations.items()},
                   data.get("missing_vehicles", {}), data["schema"])


@dataclass(frozen=True)
class AgentBindingV02:
    agent_id: str
    vehicle_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.agent_id, "agent_id")
        if not self.vehicle_ids or len(set(self.vehicle_ids)) != len(self.vehicle_ids):
            raise ContractValidationError("vehicle_ids must be non-empty and unique")
        for vehicle_id in self.vehicle_ids:
            _text(vehicle_id, "vehicle_id")

    def to_dict(self) -> dict[str, object]:
        return {"agent_id": self.agent_id, "vehicle_ids": list(self.vehicle_ids)}

    @classmethod
    def from_dict(cls, value: object) -> AgentBindingV02:
        data = _object(value, "agent binding")
        fields = {"agent_id", "vehicle_ids"}
        _fields(data, fields, fields, "agent binding")
        if not isinstance(data["vehicle_ids"], list):
            raise ContractValidationError("vehicle_ids must be an array")
        return cls(data["agent_id"], tuple(data["vehicle_ids"]))


@dataclass(frozen=True)
class ScenarioSpecV02:
    scenario_id: str
    version: str
    checksum: str
    resources: Mapping[str, str]
    seed: int | None
    initial_states: Mapping[str, object]
    truth_access: str = "task_evaluator"
    schema: str = PLATFORM_SCHEMA_V02

    def __post_init__(self) -> None:
        _schema(self.schema)
        for name in ("scenario_id", "version", "checksum"):
            _text(getattr(self, name), name)
        if self.seed is not None:
            _integer(self.seed, "seed")
        resources = _object(self.resources, "resources")
        for key, path in resources.items():
            _text(key, "resource ID"); _path(path, "resource path")
        object.__setattr__(self, "resources", resources)
        object.__setattr__(self, "initial_states", _object(self.initial_states, "initial_states"))
        if self.truth_access not in ("none", "task_evaluator"):
            raise ContractValidationError("invalid truth_access")

    def to_dict(self) -> dict[str, object]:
        return dict(schema=self.schema, scenario_id=self.scenario_id, version=self.version,
                    checksum=self.checksum, resources=self.resources, seed=self.seed,
                    initial_states=self.initial_states, truth_access=self.truth_access)

    @classmethod
    def from_dict(cls, value: object) -> ScenarioSpecV02:
        data = _object(value, "scenario")
        fields = {"schema", "scenario_id", "version", "checksum", "resources", "seed", "initial_states", "truth_access"}
        _fields(data, fields, fields - {"truth_access"}, "scenario")
        return cls(data["scenario_id"], data["version"], data["checksum"], data["resources"],
                   data["seed"], data["initial_states"], data.get("truth_access", "task_evaluator"), data["schema"])


@dataclass(frozen=True)
class CapabilitySetV02:
    action_kinds: tuple[str, ...]
    sensor_types: tuple[str, ...]
    sensor_resources: Mapping[str, str]
    coordinate_frame: str
    time_bases: tuple[str, ...]
    max_vehicles: int
    scenario_operations: tuple[str, ...]
    truth_access: bool
    bounded_execution: bool
    schema: str = PLATFORM_SCHEMA_V02

    def __post_init__(self) -> None:
        _schema(self.schema)
        for value in self.action_kinds:
            _namespaced(value, "action kind")
        for value in self.sensor_types:
            _namespaced(value, "sensor type")
        resources = _object(self.sensor_resources, "sensor_resources")
        for key, value in resources.items():
            _text(key, "sensor resource ID"); _namespaced(value, "sensor resource kind")
        object.__setattr__(self, "sensor_resources", resources)
        _text(self.coordinate_frame, "coordinate_frame")
        for value in self.time_bases:
            _text(value, "time base")
        if isinstance(self.max_vehicles, bool) or not isinstance(self.max_vehicles, int) or self.max_vehicles < 1:
            raise ContractValidationError("max_vehicles must be positive")
        for value in self.scenario_operations:
            _text(value, "scenario operation")
        if not isinstance(self.truth_access, bool) or not isinstance(self.bounded_execution, bool):
            raise ContractValidationError("capability flags must be boolean")

    def to_dict(self) -> dict[str, object]:
        return dict(schema=self.schema, action_kinds=list(self.action_kinds), sensor_types=list(self.sensor_types),
                    sensor_resources=self.sensor_resources, coordinate_frame=self.coordinate_frame,
                    time_bases=list(self.time_bases), max_vehicles=self.max_vehicles,
                    scenario_operations=list(self.scenario_operations), truth_access=self.truth_access,
                    bounded_execution=self.bounded_execution)

    @classmethod
    def from_dict(cls, value: object) -> CapabilitySetV02:
        data = _object(value, "capabilities")
        fields = {"schema", "action_kinds", "sensor_types", "sensor_resources", "coordinate_frame", "time_bases", "max_vehicles", "scenario_operations", "truth_access", "bounded_execution"}
        _fields(data, fields, fields, "capabilities")
        for key in ("action_kinds", "sensor_types", "time_bases", "scenario_operations"):
            if not isinstance(data[key], list):
                raise ContractValidationError(f"{key} must be an array")
        return cls(tuple(data["action_kinds"]), tuple(data["sensor_types"]), data["sensor_resources"],
                   data["coordinate_frame"], tuple(data["time_bases"]), data["max_vehicles"],
                   tuple(data["scenario_operations"]), data["truth_access"], data["bounded_execution"], data["schema"])
