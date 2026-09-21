"""Strict, JSON-safe platform contracts for one single-vehicle episode."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Mapping

PLATFORM_SCHEMA_VERSION = "drone.platform.contract/v0.1"


class ContractValidationError(ValueError):
    """Raised before malformed data can reach an Agent, Task, or Backend."""


class PlatformContractException(RuntimeError):
    """Structured failed read for interfaces that have no result envelope."""
    def __init__(self, error: "ContractError") -> None:
        super().__init__(error.message)
        self.error = error


class ActionKind(str, Enum):
    MOVE_TO = "move_to"
    HOVER = "hover"


class TerminationReason(str, Enum):
    SUCCESS = "success"
    TASK_FAILED = "task_failed"
    TIMEOUT = "timeout"
    BACKEND_ERROR = "backend_error"
    AGENT_ERROR = "agent_error"
    TASK_ERROR = "task_error"
    EVALUATOR_ERROR = "evaluator_error"
    CANCELLED = "cancelled"
    INITIALIZATION_ERROR = "initialization_error"


class EventSource(str, Enum):
    RUNNER = "runner"
    AGENT = "agent"
    BACKEND = "backend"
    TASK = "task"
    EVALUATOR = "evaluator"


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractValidationError(f"{name} must be a finite number")
    return float(value)


def _integer(value: object, name: str, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractValidationError(f"{name} must be a non-negative integer")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{name} must be a non-empty string")
    return value


def _object(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError(f"{name} must be an object")
    return value


def _keys(data: Mapping[str, Any], allowed: set[str], name: str, required: set[str]) -> None:
    unknown, missing = set(data) - allowed, required - set(data)
    if unknown:
        raise ContractValidationError(f"{name} has unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        raise ContractValidationError(f"{name} is missing fields: {', '.join(sorted(missing))}")


def _json(value: object, name: str) -> Any:
    try:
        return json.loads(json.dumps(value, allow_nan=False, sort_keys=True))
    except (TypeError, ValueError) as exc:
        raise ContractValidationError(f"{name} must be JSON-safe (no binary or non-finite values)") from exc


def _episode_relative_path(value: object, name: str) -> str:
    text = _text(value, name)
    if "\\" in text or ":" in text:
        raise ContractValidationError(f"{name} must use safe POSIX relative separators")
    path = PurePosixPath(text)
    if path.is_absolute() or not path.parts or any(part in (".", "..") for part in path.parts):
        raise ContractValidationError(f"{name} must stay inside the episode directory")
    return text


@dataclass(frozen=True)
class PositionNed:
    """World NED metres from the backend-documented world origin."""
    north_m: float
    east_m: float
    down_m: float

    def __post_init__(self) -> None:
        for name in ("north_m", "east_m", "down_m"):
            object.__setattr__(self, name, _finite(getattr(self, name), name))

    def to_dict(self) -> dict[str, float]:
        return {"north_m": self.north_m, "east_m": self.east_m, "down_m": self.down_m}

    @classmethod
    def from_dict(cls, value: object) -> "PositionNed":
        data = _object(value, "position_ned")
        fields = {"north_m", "east_m", "down_m"}
        _keys(data, fields, "position_ned", fields)
        return cls(data["north_m"], data["east_m"], data["down_m"])


@dataclass(frozen=True)
class ContractError:
    code: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        _text(self.code, "error.code")
        _text(self.message, "error.message")
        if not isinstance(self.retryable, bool):
            raise ContractValidationError("error.retryable must be boolean")

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}

    @classmethod
    def from_dict(cls, value: object) -> "ContractError":
        data = _object(value, "error")
        _keys(data, {"code", "message", "retryable"}, "error", {"code", "message"})
        return cls(data["code"], data["message"], data.get("retryable", False))


@dataclass(frozen=True)
class BackendConfig:
    backend_type: str
    vehicle_id: str
    connection: Mapping[str, object] = field(default_factory=dict)
    resource_root: str | None = None

    def __post_init__(self) -> None:
        _text(self.backend_type, "backend_type")
        _text(self.vehicle_id, "vehicle_id")
        object.__setattr__(self, "connection", _json(self.connection, "connection"))
        if self.resource_root is not None:
            _text(self.resource_root, "resource_root")

    def to_dict(self) -> dict[str, object]:
        return {"backend_type": self.backend_type, "vehicle_id": self.vehicle_id, "connection": self.connection, "resource_root": self.resource_root}

    @classmethod
    def from_dict(cls, value: object) -> "BackendConfig":
        data = _object(value, "backend_config")
        _keys(data, {"backend_type", "vehicle_id", "connection", "resource_root"}, "backend_config", {"backend_type", "vehicle_id"})
        return cls(data["backend_type"], data["vehicle_id"], data.get("connection", {}), data.get("resource_root"))


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    task_type: str
    time_budget_s: float
    home_position_ned: PositionNed
    altitude_reference: str
    parameters: Mapping[str, object] = field(default_factory=dict)
    initial_state: Mapping[str, object] = field(default_factory=dict)
    seed: int | None = None
    schema: str = PLATFORM_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _text(self.task_id, "task_id")
        _text(self.task_type, "task_type")
        object.__setattr__(self, "time_budget_s", _finite(self.time_budget_s, "time_budget_s"))
        if self.time_budget_s <= 0:
            raise ContractValidationError("time_budget_s must be positive")
        if self.altitude_reference != "relative_to_home":
            raise ContractValidationError("altitude_reference must be relative_to_home in v0.1")
        object.__setattr__(self, "parameters", _json(self.parameters, "parameters"))
        object.__setattr__(self, "initial_state", _json(self.initial_state, "initial_state"))
        if self.seed is not None and (isinstance(self.seed, bool) or not isinstance(self.seed, int)):
            raise ContractValidationError("seed must be an integer or null")
        if self.schema != PLATFORM_SCHEMA_VERSION:
            raise ContractValidationError("unsupported TaskSpec schema")

    def to_dict(self) -> dict[str, object]:
        return {"schema": self.schema, "task_id": self.task_id, "task_type": self.task_type, "time_budget_s": self.time_budget_s, "home_position_ned": self.home_position_ned.to_dict(), "altitude_reference": self.altitude_reference, "parameters": self.parameters, "initial_state": self.initial_state, "seed": self.seed}

    @classmethod
    def from_dict(cls, value: object) -> "TaskSpec":
        data = _object(value, "task_spec")
        fields = {"schema", "task_id", "task_type", "time_budget_s", "home_position_ned", "altitude_reference", "parameters", "initial_state", "seed"}
        _keys(data, fields, "task_spec", {"schema", "task_id", "task_type", "time_budget_s", "home_position_ned", "altitude_reference"})
        return cls(data["task_id"], data["task_type"], data["time_budget_s"], PositionNed.from_dict(data["home_position_ned"]), data["altitude_reference"], data.get("parameters", {}), data.get("initial_state", {}), data.get("seed"), data["schema"])


@dataclass(frozen=True)
class SensorReference:
    """A reference to a separate binary file; binary data never enters episode JSON."""
    sensor_id: str
    kind: str
    relative_path: str
    captured_wall_time_ns: int

    def __post_init__(self) -> None:
        _text(self.sensor_id, "sensor_id"); _text(self.kind, "sensor kind"); _text(self.relative_path, "sensor relative_path")
        _episode_relative_path(self.relative_path, "sensor relative_path")
        _integer(self.captured_wall_time_ns, "captured_wall_time_ns")

    def to_dict(self) -> dict[str, object]:
        return {"sensor_id": self.sensor_id, "kind": self.kind, "relative_path": self.relative_path, "captured_wall_time_ns": self.captured_wall_time_ns}

    @classmethod
    def from_dict(cls, value: object) -> "SensorReference":
        data = _object(value, "sensor_reference")
        fields = {"sensor_id", "kind", "relative_path", "captured_wall_time_ns"}
        _keys(data, fields, "sensor_reference", fields)
        return cls(data["sensor_id"], data["kind"], data["relative_path"], data["captured_wall_time_ns"])


@dataclass(frozen=True)
class PlatformObservation:
    sequence: int
    vehicle_id: str
    position_ned: PositionNed
    velocity_ned_mps: tuple[float, float, float]
    wall_time_ns: int
    simulator_time_ns: int | None = None
    sensors: tuple[SensorReference, ...] = ()
    missing_sensors: Mapping[str, str] = field(default_factory=dict)
    schema: str = PLATFORM_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _integer(self.sequence, "sequence"); _text(self.vehicle_id, "vehicle_id")
        if len(self.velocity_ned_mps) != 3:
            raise ContractValidationError("velocity_ned_mps must have three NED values")
        object.__setattr__(self, "velocity_ned_mps", tuple(_finite(v, "velocity_ned_mps") for v in self.velocity_ned_mps))
        _integer(self.wall_time_ns, "wall_time_ns"); _integer(self.simulator_time_ns, "simulator_time_ns", optional=True)
        object.__setattr__(self, "missing_sensors", _json(self.missing_sensors, "missing_sensors"))
        if self.schema != PLATFORM_SCHEMA_VERSION:
            raise ContractValidationError("unsupported PlatformObservation schema")

    def to_dict(self) -> dict[str, object]:
        return {"schema": self.schema, "sequence": self.sequence, "vehicle_id": self.vehicle_id, "position_ned": self.position_ned.to_dict(), "velocity_ned_mps": list(self.velocity_ned_mps), "wall_time_ns": self.wall_time_ns, "simulator_time_ns": self.simulator_time_ns, "sensors": [s.to_dict() for s in self.sensors], "missing_sensors": self.missing_sensors}

    @classmethod
    def from_dict(cls, value: object) -> "PlatformObservation":
        data = _object(value, "platform_observation")
        fields = {"schema", "sequence", "vehicle_id", "position_ned", "velocity_ned_mps", "wall_time_ns", "simulator_time_ns", "sensors", "missing_sensors"}
        _keys(data, fields, "platform_observation", {"schema", "sequence", "vehicle_id", "position_ned", "velocity_ned_mps", "wall_time_ns"})
        velocity, sensors = data["velocity_ned_mps"], data.get("sensors", [])
        if not isinstance(velocity, list) or not isinstance(sensors, list):
            raise ContractValidationError("observation velocity and sensors must be arrays")
        return cls(data["sequence"], data["vehicle_id"], PositionNed.from_dict(data["position_ned"]), tuple(velocity), data["wall_time_ns"], data.get("simulator_time_ns"), tuple(SensorReference.from_dict(s) for s in sensors), data.get("missing_sensors", {}), data["schema"])


@dataclass(frozen=True)
class Action:
    action_id: str
    kind: ActionKind
    vehicle_id: str
    deadline_s: float
    target_position_ned: PositionNed | None = None
    schema: str = PLATFORM_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _text(self.action_id, "action_id")
        if not isinstance(self.kind, ActionKind):
            raise ContractValidationError("kind must be an ActionKind")
        _text(self.vehicle_id, "vehicle_id")
        object.__setattr__(self, "deadline_s", _finite(self.deadline_s, "deadline_s"))
        if self.deadline_s <= 0:
            raise ContractValidationError("deadline_s must be positive")
        if (self.kind is ActionKind.MOVE_TO) != (self.target_position_ned is not None):
            raise ContractValidationError("move_to requires target_position_ned; hover forbids it")
        if self.schema != PLATFORM_SCHEMA_VERSION:
            raise ContractValidationError("unsupported Action schema")

    def to_dict(self) -> dict[str, object]:
        return {"schema": self.schema, "action_id": self.action_id, "kind": self.kind.value, "vehicle_id": self.vehicle_id, "deadline_s": self.deadline_s, "target_position_ned": None if self.target_position_ned is None else self.target_position_ned.to_dict()}

    @classmethod
    def from_dict(cls, value: object) -> "Action":
        data = _object(value, "action")
        fields = {"schema", "action_id", "kind", "vehicle_id", "deadline_s", "target_position_ned"}
        _keys(data, fields, "action", fields)
        try:
            kind = ActionKind(data["kind"])
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("unknown action kind") from exc
        target = data["target_position_ned"]
        return cls(data["action_id"], kind, data["vehicle_id"], data["deadline_s"], None if target is None else PositionNed.from_dict(target), data["schema"])


@dataclass(frozen=True)
class ExecutionResult:
    """Backend command status, never evidence that a task destination was reached."""
    action_id: str
    accepted: bool
    completed: bool
    succeeded: bool
    wall_time_ns: int
    error: ContractError | None = None

    def __post_init__(self) -> None:
        _text(self.action_id, "execution action_id")
        if not all(isinstance(v, bool) for v in (self.accepted, self.completed, self.succeeded)):
            raise ContractValidationError("execution flags must be boolean")
        if self.succeeded and not (self.accepted and self.completed):
            raise ContractValidationError("successful execution must be accepted and completed")
        if self.succeeded and self.error is not None:
            raise ContractValidationError("successful execution cannot have an error")
        _integer(self.wall_time_ns, "execution wall_time_ns")

    def to_dict(self) -> dict[str, object]:
        return {"action_id": self.action_id, "accepted": self.accepted, "completed": self.completed, "succeeded": self.succeeded, "wall_time_ns": self.wall_time_ns, "error": None if self.error is None else self.error.to_dict()}

    @classmethod
    def from_dict(cls, value: object) -> "ExecutionResult":
        data = _object(value, "execution_result")
        fields = {"action_id", "accepted", "completed", "succeeded", "wall_time_ns", "error"}
        _keys(data, fields, "execution_result", fields)
        error = data["error"]
        return cls(data["action_id"], data["accepted"], data["completed"], data["succeeded"], data["wall_time_ns"], None if error is None else ContractError.from_dict(error))


@dataclass(frozen=True)
class StepRecord:
    sequence: int
    observation_before: PlatformObservation
    action: Action
    execution: ExecutionResult
    observation_after: PlatformObservation

    def __post_init__(self) -> None:
        _integer(self.sequence, "step sequence")
        if self.action.action_id != self.execution.action_id:
            raise ContractValidationError("step execution must reference its action")
        if self.observation_before.sequence != self.sequence or self.observation_after.sequence != self.sequence + 1:
            raise ContractValidationError("step must contain before sequence N and after sequence N+1")
        if self.observation_before.vehicle_id != self.observation_after.vehicle_id:
            raise ContractValidationError("step observations must belong to one vehicle")


@dataclass(frozen=True)
class TaskProgress:
    done: bool
    success: bool
    reason: TerminationReason | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.done, bool) or not isinstance(self.success, bool):
            raise ContractValidationError("task progress flags must be boolean")
        if self.success and (not self.done or self.reason is not TerminationReason.SUCCESS):
            raise ContractValidationError("success requires done=true and reason=success")


@dataclass(frozen=True)
class EpisodeEvent:
    sequence: int
    source: EventSource
    kind: str
    wall_time_ns: int
    fields: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _integer(self.sequence, "event sequence")
        if not isinstance(self.source, EventSource):
            raise ContractValidationError("event source must be an EventSource")
        _text(self.kind, "event kind"); _integer(self.wall_time_ns, "event wall_time_ns")
        object.__setattr__(self, "fields", _json(self.fields, "event fields"))


@dataclass(frozen=True)
class CleanupResult:
    attempted: bool
    succeeded: bool | None
    error: ContractError | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.attempted, bool) or (self.succeeded is not None and not isinstance(self.succeeded, bool)) or self.attempted != (self.succeeded is not None):
            raise ContractValidationError("cleanup requires null result exactly when not attempted")
        if not self.attempted and self.error is not None:
            raise ContractValidationError("unattempted cleanup cannot have an error")
        if self.succeeded is True and self.error is not None:
            raise ContractValidationError("successful cleanup cannot have an error")

    def to_dict(self) -> dict[str, object]:
        return {"attempted": self.attempted, "succeeded": self.succeeded, "error": None if self.error is None else self.error.to_dict()}

    @classmethod
    def from_dict(cls, value: object) -> "CleanupResult":
        data = _object(value, "cleanup")
        fields = {"attempted", "succeeded", "error"}
        _keys(data, fields, "cleanup", fields)
        error = data["error"]
        return cls(data["attempted"], data["succeeded"], None if error is None else ContractError.from_dict(error))


@dataclass(frozen=True)
class EpisodeResult:
    episode_id: str
    task_id: str
    termination_reason: TerminationReason
    success: bool
    final_observation: PlatformObservation
    cleanup: CleanupResult
    metrics: Mapping[str, float] = field(default_factory=dict)
    record_path: str | None = None
    schema: str = PLATFORM_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _text(self.episode_id, "episode_id"); _text(self.task_id, "result task_id")
        if not isinstance(self.success, bool):
            raise ContractValidationError("result success must be boolean")
        if not isinstance(self.termination_reason, TerminationReason) or self.success != (self.termination_reason is TerminationReason.SUCCESS):
            raise ContractValidationError("result success must match termination_reason")
        if not isinstance(self.metrics, Mapping):
            raise ContractValidationError("metrics must be an object")
        checked_metrics: dict[str, float] = {}
        for key, value in self.metrics.items():
            _text(key, "metric name")
            checked_metrics[key] = _finite(value, f"metric {key}")
        object.__setattr__(self, "metrics", checked_metrics)
        if self.record_path is not None:
            _episode_relative_path(self.record_path, "record_path")
        if self.schema != PLATFORM_SCHEMA_VERSION:
            raise ContractValidationError("unsupported EpisodeResult schema")

    def to_dict(self) -> dict[str, object]:
        return {"schema": self.schema, "episode_id": self.episode_id, "task_id": self.task_id, "termination_reason": self.termination_reason.value, "success": self.success, "final_observation": self.final_observation.to_dict(), "cleanup": self.cleanup.to_dict(), "metrics": self.metrics, "record_path": self.record_path}

    @classmethod
    def from_dict(cls, value: object) -> "EpisodeResult":
        data = _object(value, "episode_result")
        fields = {"schema", "episode_id", "task_id", "termination_reason", "success", "final_observation", "cleanup", "metrics", "record_path"}
        _keys(data, fields, "episode_result", {"schema", "episode_id", "task_id", "termination_reason", "success", "final_observation", "cleanup"})
        try:
            reason = TerminationReason(data["termination_reason"])
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("unknown termination reason") from exc
        return cls(data["episode_id"], data["task_id"], reason, data["success"], PlatformObservation.from_dict(data["final_observation"]), CleanupResult.from_dict(data["cleanup"]), data.get("metrics", {}), data.get("record_path"), data["schema"])
