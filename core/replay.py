"""Read and validate a recorded episode; never resubmit actions to a simulator."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path

from contracts import (
    Action, BackendConfig, EpisodeEvent, EpisodeResult, EventSource,
    ExecutionResult, PlatformObservation, StepRecord, TaskSpec, TerminationReason,
)


@dataclass(frozen=True)
class EpisodeReplay:
    directory: Path
    task: TaskSpec
    backend: BackendConfig
    result: EpisodeResult
    steps: tuple[StepRecord, ...]
    events: tuple[EpisodeEvent, ...]

    def to_dict(self) -> dict[str, object]:
        positions = [step.observation_before.position_ned.to_dict() for step in self.steps[:1]]
        positions.extend(step.observation_after.position_ned.to_dict() for step in self.steps)
        if not positions:
            positions.append(self.result.final_observation.position_ned.to_dict())
        return {
            "episode_id": self.result.episode_id,
            "task_id": self.task.task_id,
            "termination_reason": self.result.termination_reason.value,
            "task_success": self.result.success,
            "cleanup": self.result.cleanup.to_dict(),
            "step_count": len(self.steps),
            "event_count": len(self.events),
            "positions_ned": positions,
            "metrics": dict(self.result.metrics),
        }


def _json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _lines(path: Path) -> list[object]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_episode(directory: str | Path) -> EpisodeReplay:
    root = Path(directory)
    if (root / "INCOMPLETE").exists():
        raise ValueError("episode recording is incomplete")
    task = TaskSpec.from_dict(_json(root / "task.json"))
    backend = BackendConfig.from_dict(_json(root / "backend.json"))
    result = EpisodeResult.from_dict(_json(root / "result.json"))
    if result.episode_id != root.name or result.task_id != task.task_id:
        raise ValueError("episode result identity does not match its directory or task")
    if result.record_path != "trajectory.jsonl":
        raise ValueError("episode result does not reference a complete trajectory")
    steps: list[StepRecord] = []
    for data in _lines(root / "trajectory.jsonl"):
        step = StepRecord(
            data["sequence"],
            PlatformObservation.from_dict(data["observation_before"]),
            Action.from_dict(data["action"]),
            ExecutionResult.from_dict(data["execution"]),
            PlatformObservation.from_dict(data["observation_after"]),
        )
        if step.sequence != len(steps):
            raise ValueError("trajectory step sequences are not contiguous")
        if steps and step.observation_before.sequence != steps[-1].observation_after.sequence:
            raise ValueError("trajectory observations are not contiguous")
        steps.append(step)
    if steps and result.termination_reason is TerminationReason.SUCCESS and result.final_observation != steps[-1].observation_after:
        raise ValueError("successful episode final observation does not match its final step")
    events: list[EpisodeEvent] = []
    for data in _lines(root / "events.jsonl"):
        event = EpisodeEvent(data["sequence"], EventSource(data["source"]), data["kind"], data["wall_time_ns"], data.get("fields", {}))
        if event.sequence != len(events):
            raise ValueError("event sequences are not contiguous")
        events.append(event)
    for observation in [result.final_observation, *(step.observation_before for step in steps), *(step.observation_after for step in steps)]:
        for sensor in observation.sensors:
            file_path = root / sensor.relative_path
            if not file_path.resolve().is_relative_to(root.resolve()) or not file_path.is_file():
                raise ValueError(f"missing or unsafe sensor artifact: {sensor.relative_path}")
    return EpisodeReplay(root, task, backend, result, tuple(steps), tuple(events))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(read_episode(args.episode).to_dict(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
