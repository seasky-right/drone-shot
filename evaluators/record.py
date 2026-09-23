"""Versioned offline episode input and result.json output for ReachPoint."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from contracts import (
    Action, CleanupResult, ContractError, ContractValidationError, EpisodeEvent, EventSource,
    ExecutionResult, PlatformObservation, StepRecord, TaskProgress, TaskSpec,
    TerminationReason,
)
from evaluators.reach_point import ReachPointEvaluator

RECORD_SCHEMA = "drone.evaluation.record/v0.1"
RESULT_SCHEMA = "drone.evaluation.result/v0.1"
SCORING_POLICY_VERSION = "reach_point_scoring/v0.1"


def _object(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ContractValidationError(f"{name} must be an object")
    return value


def _keys(value: Mapping[str, Any], required: set[str], optional: set[str], name: str) -> None:
    missing, unknown = required - set(value), set(value) - required - optional
    if missing or unknown:
        raise ContractValidationError(f"{name}: missing={sorted(missing)}, unknown={sorted(unknown)}")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{name} must be non-empty text")
    return value


def _reason(value: object) -> TerminationReason:
    try:
        return TerminationReason(value)
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("invalid termination reason") from exc


@dataclass(frozen=True)
class RecordedEpisode:
    benchmark_id: str
    case_id: str
    agent_id: str
    episode_id: str
    repeat_index: int
    task_spec: TaskSpec
    initial_observation: PlatformObservation
    steps: tuple[StepRecord, ...]
    step_elapsed_s: tuple[float, ...]
    progress: TaskProgress
    termination_reason: TerminationReason
    events: tuple[EpisodeEvent, ...] | None
    cleanup: CleanupResult
    environment: Mapping[str, Any]
    error: ContractError | None

    @classmethod
    def from_dict(cls, value: object) -> "RecordedEpisode":
        data = _object(value, "episode record")
        required = {"schema", "benchmark_id", "case_id", "agent_id", "episode_id",
                    "repeat_index", "task_spec", "initial_observation", "steps",
                    "progress", "termination_reason", "events", "cleanup", "environment"}
        _keys(data, required, {"error"}, "episode record")
        if data["schema"] != RECORD_SCHEMA:
            raise ContractValidationError("unsupported episode record schema")
        repeat = data["repeat_index"]
        if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 0:
            raise ContractValidationError("repeat_index must be a non-negative integer")
        if not isinstance(data["steps"], list):
            raise ContractValidationError("steps must be an array")
        steps, elapsed = [], []
        for raw in data["steps"]:
            item = _object(raw, "step")
            _keys(item, {"sequence", "observation_before", "action", "execution",
                         "observation_after", "elapsed_monotonic_s"}, set(), "step")
            steps.append(StepRecord(
                item["sequence"], PlatformObservation.from_dict(item["observation_before"]),
                Action.from_dict(item["action"]), ExecutionResult.from_dict(item["execution"]),
                PlatformObservation.from_dict(item["observation_after"])))
            elapsed.append(item["elapsed_monotonic_s"])
        progress_data = _object(data["progress"], "progress")
        _keys(progress_data, {"done", "success", "reason"}, set(), "progress")
        progress = TaskProgress(progress_data["done"], progress_data["success"],
                                None if progress_data["reason"] is None else _reason(progress_data["reason"]))
        raw_events = data["events"]
        if raw_events is not None and not isinstance(raw_events, list):
            raise ContractValidationError("events must be an array or null")
        events = None
        if raw_events is not None:
            parsed = []
            for raw in raw_events:
                item = _object(raw, "event")
                _keys(item, {"sequence", "source", "kind", "wall_time_ns", "fields"},
                      set(), "event")
                try:
                    source = EventSource(item["source"])
                except (TypeError, ValueError) as exc:
                    raise ContractValidationError("invalid event source") from exc
                parsed.append(EpisodeEvent(item["sequence"], source, item["kind"],
                                           item["wall_time_ns"], item["fields"]))
            events = tuple(parsed)
        environment = _object(data["environment"], "environment")
        error = None if data.get("error") is None else ContractError.from_dict(data["error"])
        try:
            json.dumps(environment, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("environment must be JSON-safe") from exc
        reason = _reason(data["termination_reason"])
        if reason.value.endswith("_error") and error is None:
            raise ContractValidationError("error termination requires structured error details")
        if events is not None:
            if any(b.sequence <= a.sequence or b.wall_time_ns < a.wall_time_ns
                   for a, b in zip(events, events[1:])):
                raise ContractValidationError("events must have increasing sequence and time")
        return cls(*(_text(data[k], k) for k in ("benchmark_id", "case_id", "agent_id", "episode_id")),
                   repeat, TaskSpec.from_dict(data["task_spec"]),
                   PlatformObservation.from_dict(data["initial_observation"]),
                   tuple(steps), tuple(elapsed), progress, reason,
                   events, CleanupResult.from_dict(data["cleanup"]), environment, error)


def evaluate_record(record: RecordedEpisode, record_path: str) -> dict[str, Any]:
    """Produce a JSON-safe, reproducible result from one recorded episode."""
    evaluation = ReachPointEvaluator(record.task_spec).evaluate_episode(
        record.initial_observation, record.steps, record.step_elapsed_s,
        record.progress, record.termination_reason, record.events, record.cleanup)
    last = record.steps[-1].observation_after if record.steps else record.initial_observation
    return {
        "schema": RESULT_SCHEMA,
        "benchmark_id": record.benchmark_id,
        "case_id": record.case_id,
        "agent_id": record.agent_id,
        "episode_id": record.episode_id,
        "repeat_index": record.repeat_index,
        "task_spec": record.task_spec.to_dict(),
        "termination_reason": record.termination_reason.value,
        "success": record.termination_reason is TerminationReason.SUCCESS,
        "metrics": evaluation.metrics,
        "scores": {"goal": evaluation.goal_score, "time": evaluation.time_score,
                   "total": evaluation.score},
        "safety": {"status": evaluation.safety_status,
                   "rank_eligible": evaluation.rank_eligible},
        "cleanup": record.cleanup.to_dict(),
        "trajectory_summary": {
            "observations": len(record.steps) + 1,
            "start_position_ned": record.initial_observation.position_ned.to_dict(),
            "end_position_ned": last.position_ned.to_dict(),
            "path_length_m": evaluation.metrics["path_length_m"],
        },
        "evaluator_version": evaluation.evaluator_version,
        "scoring_policy_version": SCORING_POLICY_VERSION,
        "environment": dict(record.environment),
        "error": None if record.error is None else record.error.to_dict(),
        "record_path": record_path,
    }


def read_record(path: Path) -> RecordedEpisode:
    return RecordedEpisode.from_dict(json.loads(path.read_text(encoding="utf-8")))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(path)
