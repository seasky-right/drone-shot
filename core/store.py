"""Task-independent, episode-scoped artifact and result storage."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4


class StoreError(RuntimeError):
    """An episode record could not be written or safely read."""


def _component(value: str) -> str:
    if (not isinstance(value, str) or not value or value in (".", "..")
            or "/" in value or "\\" in value or ":" in value
            or any(ord(char) < 32 for char in value)):
        raise ValueError("episode_id must be one safe path component")
    return value


def _relative(value: str) -> Path:
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or any(ord(char) < 32 for char in value)):
        raise ValueError("artifact path must be a safe POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in (".", "..") for part in value.split("/")):
        raise ValueError("artifact path must stay inside its episode")
    return Path(*path.parts)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


class EpisodeStore:
    """One writer per episode; readers only accept records without INCOMPLETE."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.directory: Path | None = None
        self.failed = False
        self.failure_message: str | None = None
        self.finished = False

    def _path(self, relative_path: str) -> Path:
        if self.directory is None:
            raise StoreError("episode store has not started")
        path = self.directory / _relative(relative_path)
        if not path.resolve().is_relative_to(self.directory.resolve()):
            raise ValueError("artifact path escapes the episode directory")
        return path

    def _fail(self, exc: OSError) -> None:
        self.failed = True
        self.failure_message = str(exc)
        raise StoreError(str(exc)) from exc

    def _atomic(self, path: Path, data: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            self._fail(exc)

    def start(self, episode_id: str, *, metadata: dict[str, object] | None = None,
              task: object | None = None, backend: object | None = None) -> Path:
        if self.directory is not None:
            raise StoreError("episode store is already in use")
        directory = self.root / _component(episode_id)
        if not directory.resolve().is_relative_to(self.root):
            raise ValueError("episode directory escapes the store root")
        try:
            directory.mkdir(parents=True, exist_ok=False)
            self.directory = directory
            self._atomic(self._path("INCOMPLETE"), b"episode recording has not finished\n")
            self._atomic(self._path("run.json"), _json_bytes(metadata or {}))
            if task is not None:
                self._atomic(self._path("task.json"), _json_bytes(task))
            if backend is not None:
                self._atomic(self._path("backend.json"), _json_bytes(backend))
            self._path("trajectory.jsonl").touch(exist_ok=False)
            self._path("events.jsonl").touch(exist_ok=False)
        except OSError as exc:
            self._fail(exc)
        return directory

    def write_artifact(self, relative_path: str, content: bytes) -> str:
        if self.finished:
            raise StoreError("episode store is finished")
        if not isinstance(content, bytes):
            raise TypeError("artifact content must be bytes")
        if relative_path in {"INCOMPLETE", "result.json", "run.json", "task.json", "backend.json", "trajectory.jsonl", "events.jsonl"}:
            raise ValueError("artifact path is reserved for episode records")
        path = self._path(relative_path)
        if path.exists():
            raise FileExistsError(path)
        self._atomic(path, content)
        return relative_path

    def write_bytes(self, relative_path: str, content: bytes) -> str:
        """Implement the public v0.2 ArtifactWriter shape."""
        return self.write_artifact(relative_path, content)

    def append(self, relative_path: str, value: object) -> None:
        if self.finished:
            raise StoreError("episode store is finished")
        if relative_path not in ("trajectory.jsonl", "events.jsonl"):
            raise ValueError("append is limited to episode journals")
        path = self._path(relative_path)
        try:
            data = json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n"
            with path.open("a", encoding="utf-8") as stream:
                stream.write(data)
                stream.flush()
        except OSError as exc:
            self._fail(exc)

    def finish(self, result: object) -> Path:
        if self.finished:
            raise StoreError("episode store is already finished")
        path = self._path("result.json")
        self._atomic(path, _json_bytes(result))
        self.finished = True
        if not self.failed:
            try:
                self._path("INCOMPLETE").unlink()
            except OSError as exc:
                self._fail(exc)
        return path

    @classmethod
    def read_result(cls, root: str | Path, episode_id: str) -> dict[str, object]:
        store = cls(root)
        directory = store.root / _component(episode_id)
        if not directory.resolve().is_relative_to(store.root):
            raise ValueError("episode directory escapes the store root")
        if (directory / "INCOMPLETE").exists():
            raise StoreError("episode recording is incomplete")
        result_path = directory / "result.json"
        if not result_path.resolve().is_relative_to(directory.resolve()):
            raise StoreError("episode result path escapes its directory")
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StoreError("episode result is missing or invalid") from exc
        if not isinstance(data, dict) or data.get("episode_id") != episode_id:
            raise StoreError("episode result identity mismatch")
        return data
