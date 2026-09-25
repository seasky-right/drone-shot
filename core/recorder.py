"""Episode artifacts with append-only steps and events."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contracts import (
    Action, BackendConfig, EpisodeEvent, EpisodeResult, EventSource,
    ExecutionResult, PlatformObservation, StepRecord, TaskSpec,
)

from .store import EpisodeStore, StoreError


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


class Recorder:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.failed = False
        self.failure_message: str | None = None

    def start(self, episode_id: str, task: TaskSpec, backend: BackendConfig, metadata: dict[str, object] | None = None) -> Path:
        if not episode_id or Path(episode_id).name != episode_id or episode_id in (".", ".."):
            raise ValueError("episode_id must be one safe path component")
        self.failed = False
        self.failure_message = None
        self.directory = self.root / episode_id
        self.directory.mkdir(parents=True, exist_ok=False)
        (self.directory / "INCOMPLETE").write_text("episode recording has not finished\n", encoding="utf-8")
        _write_json(self.directory / "task.json", task.to_dict())
        _write_json(self.directory / "backend.json", backend.to_dict())
        _write_json(self.directory / "run.json", metadata or {})
        (self.directory / "trajectory.jsonl").touch()
        (self.directory / "events.jsonl").touch()
        return self.directory

    def step(self, step: StepRecord) -> None:
        payload = {
            "sequence": step.sequence,
            "observation_before": step.observation_before.to_dict(),
            "action": step.action.to_dict(),
            "execution": step.execution.to_dict(),
            "observation_after": step.observation_after.to_dict(),
        }
        self._append("trajectory.jsonl", payload)

    def event(self, event: EpisodeEvent) -> None:
        self._append("events.jsonl", {
            "sequence": event.sequence, "source": event.source.value,
            "kind": event.kind, "wall_time_ns": event.wall_time_ns,
            "fields": event.fields,
        })

    def finish(self, result: EpisodeResult) -> None:
        temporary = self.directory / "result.json.tmp"
        _write_json(temporary, result.to_dict())
        temporary.replace(self.directory / "result.json")
        if not self.failed:
            (self.directory / "INCOMPLETE").unlink()

    def _append(self, name: str, value: dict[str, object]) -> None:
        if self.failed:
            return
        try:
            with (self.directory / name).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        except OSError as exc:
            self.failed = True
            self.failure_message = f"{name}: {exc}"


class RecorderStoreAdapter:
    """Run the shared session state machine through an existing Recorder."""

    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder
        self.root = recorder.root.resolve()
        self.directory: Path | None = None
        self._artifacts = EpisodeStore(self.root)

    @property
    def failed(self) -> bool:
        return self.recorder.failed or self._artifacts.failed

    def start(self, episode_id: str, *, task: object, backend: object,
              metadata: dict[str, object] | None = None) -> Path:
        self.directory = self.recorder.start(
            episode_id, TaskSpec.from_dict(task), BackendConfig.from_dict(backend), metadata)
        self._artifacts.directory = self.directory
        return self.directory

    def append(self, name: str, value: dict[str, object]) -> None:
        if name == "events.jsonl":
            self.recorder.event(EpisodeEvent(
                value["sequence"], EventSource(value["source"]), value["kind"],
                value["wall_time_ns"], value["fields"]))
        elif name == "trajectory.jsonl":
            self.recorder.step(StepRecord(
                value["sequence"],
                PlatformObservation.from_dict(value["observation_before"]),
                Action.from_dict(value["action"]),
                ExecutionResult.from_dict(value["execution"]),
                PlatformObservation.from_dict(value["observation_after"])))
        else:
            raise ValueError("append is limited to episode journals")

    def write_bytes(self, relative_path: str, content: bytes) -> str:
        try:
            return self._artifacts.write_bytes(relative_path, content)
        except StoreError as exc:
            self.recorder.failed = True
            self.recorder.failure_message = str(exc)
            raise

    def finish(self, result: dict[str, object]) -> Path:
        if self._artifacts.failed:
            self.recorder.failed = True
            self.recorder.failure_message = self._artifacts.failure_message
        self.recorder.finish(EpisodeResult.from_dict(result))
        self._artifacts.finished = True
        return self.directory / "result.json"
