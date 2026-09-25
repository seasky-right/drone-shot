"""One synchronous episode through the public interactive session lifecycle."""
from __future__ import annotations

import platform
import time
from typing import Callable, Mapping
from uuid import uuid4

from contracts import BackendConfig, EpisodeResult, TaskSpec, TerminationReason

from .recorder import Recorder, RecorderStoreAdapter
from .session import EpisodeSession, SessionError, SessionState


class EpisodeRunner:
    def __init__(self, backend, agent, task, evaluator, recorder: Recorder, *,
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock_ns: Callable[[], int] = time.time_ns,
                 provenance: Mapping[str, object] | None = None) -> None:
        self.backend, self.agent, self.task, self.evaluator = backend, agent, task, evaluator
        self.recorder, self.clock, self.wall_clock_ns = recorder, clock, wall_clock_ns
        self.provenance = dict(provenance or {})

    def run(self, spec: TaskSpec, config: BackendConfig, *, episode_id: str | None = None) -> EpisodeResult:
        episode_id = episode_id or uuid4().hex
        session = EpisodeSession(
            self.backend, self.agent, self.task, self.evaluator, self.recorder.root,
            store_factory=lambda _: RecorderStoreAdapter(self.recorder),
            deadline_reason=TerminationReason.BACKEND_ERROR,
            clock=self.clock, wall_clock_ns=self.wall_clock_ns)
        metadata = {
            "platform_contract": spec.schema, "python": platform.python_version(),
            "backend_type": config.backend_type, "agent_type": type(self.agent).__name__,
            "task_type": type(self.task).__name__, "evaluator_type": type(self.evaluator).__name__,
            "plugins": self.provenance,
            "backend_connection_recording": "omitted",
        }
        try:
            session.reset(spec, config, episode_id=episode_id, metadata=metadata)
        except SessionError as exc:
            return exc.result
        try:
            while session.state is SessionState.ACTIVE:
                outcome = session.step()
                if outcome.result is not None:
                    return outcome.result
        except KeyboardInterrupt:
            return session.stop(TerminationReason.CANCELLED)
        raise RuntimeError("episode session stopped without a result")
