"""Interactive, synchronous episode lifecycle for v0.1 platform components."""
from __future__ import annotations

import math
import platform
import time
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event
from typing import Callable, Mapping
from uuid import uuid4

from contracts import (
    Action, BackendConfig, CleanupResult, ContractError, EpisodeResult,
    ExecutionResult, PlatformContractException, PlatformObservation,
    StepRecord, TaskProgress, TaskSpec, TerminationReason,
)

from .store import EpisodeStore, StoreError
from .episode_lifecycle import EpisodeLifecycle, LifecycleState


SessionState = LifecycleState


@dataclass(frozen=True)
class StepOutcome:
    step: StepRecord | None
    result: EpisodeResult | None


class SessionError(RuntimeError):
    def __init__(self, stage: str, result: EpisodeResult) -> None:
        super().__init__(f"episode failed during {stage}: {result.termination_reason.value}")
        self.stage = stage
        self.result = result


class _ArtifactWriter:
    def __init__(self, store: EpisodeStore, active: Callable[[], bool]) -> None:
        self._store = store
        self._active = active

    def write_bytes(self, relative_path: str, content: bytes) -> str:
        if not self._active():
            raise RuntimeError("episode context is no longer active")
        return self._store.write_bytes(relative_path, content)


class SessionContext:
    """CoreContext-compatible view without component or truth access."""

    def __init__(self, episode_id: str, store: EpisodeStore,
                 emit_event: Callable[[str, Mapping[str, object]], None],
                 active: Callable[[], bool]) -> None:
        self._episode_id = episode_id
        self._artifacts = _ArtifactWriter(store, active)
        self._emit_event = emit_event

    @property
    def episode_id(self) -> str:
        return self._episode_id

    @property
    def artifacts(self) -> _ArtifactWriter:
        return self._artifacts

    def emit(self, kind: str, fields: Mapping[str, object]) -> None:
        if not isinstance(kind, str) or not kind:
            raise ValueError("event kind must be a non-empty string")
        self._emit_event(kind, fields)


def _error(exc: Exception) -> ContractError:
    if isinstance(exc, PlatformContractException):
        return exc.error
    return ContractError(type(exc).__name__, str(exc) or type(exc).__name__)


class EpisodeSession:
    """A reset/step/stop service; a new episode may start after each stop.

    Calls are synchronous. Deadlines are checked when a component call returns;
    a blocking backend must provide its own bounded calls or isolation.
    """

    def __init__(self, backend, agent, task, evaluator, store_root: str | Path, *,
                 runtime=None, max_steps: int | None = None,
                 cancel_event: Event | None = None,
                 store_factory: Callable[[Path], EpisodeStore] = EpisodeStore,
                 deadline_reason: TerminationReason = TerminationReason.TIMEOUT,
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock_ns: Callable[[], int] = time.time_ns,
                 on_event: Callable[[dict[str, object]], None] | None = None) -> None:
        if max_steps is not None and (isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1):
            raise ValueError("max_steps must be a positive integer")
        self.backend, self.agent, self.task, self.evaluator = backend, agent, task, evaluator
        self.runtime, self.max_steps = runtime, max_steps
        self._store_factory = store_factory
        self.deadline_reason = deadline_reason
        self._external_cancel_event = cancel_event
        self.store_root = Path(store_root)
        self.clock, self.wall_clock_ns, self.on_event = clock, wall_clock_ns, on_event
        self.lifecycle = self._new_lifecycle()
        self.result: EpisodeResult | None = None
        self.store: EpisodeStore | None = None
        self.context: SessionContext | None = None

    def _new_lifecycle(self) -> EpisodeLifecycle:
        return EpisodeLifecycle(self._record_event, clock=self.clock,
                                wall_clock_ns=self.wall_clock_ns,
                                on_event=self.on_event,
                                cancel_event=self._external_cancel_event)

    @property
    def state(self) -> SessionState:
        return self.lifecycle.state

    @property
    def cancel_event(self) -> Event:
        return self.lifecycle.cancel_event

    def request_cancel(self) -> None:
        """Signal cancellation; a synchronous component call returns before it takes effect."""
        self.lifecycle.request_cancel()

    def _record_event(self, event: dict[str, object]) -> None:
        try:
            self.store.append("events.jsonl", event)
        except StoreError:
            pass

    def _emit(self, source: str, kind: str, **fields: object) -> None:
        self.lifecycle.emit(source, kind, **fields)

    def _failure(self, stage: str, exc: Exception, reason: TerminationReason) -> None:
        self._emit("runner", "component_error", stage=stage, error=_error(exc).to_dict())
        self.stop(reason)

    def reset(self, spec: TaskSpec, config: BackendConfig, *, episode_id: str | None = None,
              metadata: dict[str, object] | None = None) -> PlatformObservation:
        if self.state is SessionState.ACTIVE:
            raise RuntimeError("stop the active episode before reset")
        episode_id = episode_id or uuid4().hex
        self.result = None
        self.store = self._store_factory(self.store_root)
        self.context = None
        self.lifecycle = self._new_lifecycle()
        self._steps: list[StepRecord] = []
        self._progress = TaskProgress(False, False)
        self._metrics: dict[str, float] = {}
        self._spec = spec
        self._episode_id = episode_id
        self._start = self.clock()
        self._observation = PlatformObservation(
            0, config.vehicle_id, spec.home_position_ned, (0.0, 0.0, 0.0),
            self.wall_clock_ns(), missing_sensors={
                "__platform_state__": "synthetic_from_task_home; backend reset did not provide a sample"})
        self.lifecycle.begin()
        stage = "store.start"
        try:
            effective_config = replace(config, resource_root=str(self.store.root / episode_id))
            recorded_config = replace(effective_config, connection={})
            directory = self.store.start(episode_id, task=spec.to_dict(), backend=recorded_config.to_dict(),
                                         metadata={**(metadata or {}),
                                                   "platform_contract": spec.schema,
                                                   "python": platform.python_version(),
                                                   "backend_connection_recording": "omitted"})
            self.context = SessionContext(episode_id, self.store, self._emit_context_event,
                                          lambda: self.state is SessionState.ACTIVE)
            # Runtime acquisition precedes backend reset; release runs during stop.
            if self.runtime is not None:
                stage = "runtime.acquire"
                self.lifecycle.acquire(self.runtime, episode_id, effective_config)
            stage = "backend.reset"
            self._observation = self.backend.reset(effective_config, spec)
            self._emit("backend", "reset", observation_sequence=self._observation.sequence)
            stage = "task.reset"
            self.task.reset(spec, self._observation)
            stage = "agent.reset"
            self.agent.reset(spec, self._observation)
            return self._observation
        except (Exception, KeyboardInterrupt) as exc:
            if self.store.directory is not None:
                if stage == "backend.reset":
                    self._emit("runner", "synthetic_final_observation",
                               origin="task_spec.home_position_ned")
                reason = (TerminationReason.CANCELLED if isinstance(exc, KeyboardInterrupt)
                          else TerminationReason.INITIALIZATION_ERROR)
                self._failure(stage, exc, reason)
                raise SessionError(stage, self.result) from exc
            self.lifecycle.end()
            raise

    def _emit_context_event(self, kind: str, fields: Mapping[str, object]) -> None:
        if self.state is not SessionState.ACTIVE:
            raise RuntimeError("episode context is no longer active")
        self._emit("runner", "plugin_event", name=kind, payload=dict(fields))

    def step(self, action: Action | None = None) -> StepOutcome:
        if self.state is not SessionState.ACTIVE:
            raise RuntimeError("reset an episode before stepping")
        pending = self.lifecycle.checkpoint(
            self.clock() - self._start, self._spec.time_budget_s,
            steps=len(self._steps), max_steps=self.max_steps)
        if pending is not None:
            return StepOutcome(None, self.stop(TerminationReason(pending)))
        try:
            before = self.backend.observe()
            self._observation = before
        except KeyboardInterrupt:
            return StepOutcome(None, self.stop(TerminationReason.CANCELLED))
        except Exception as exc:
            self._failure("backend.observe_before", exc, TerminationReason.BACKEND_ERROR)
            return StepOutcome(None, self.result)
        if self.cancel_event.is_set():
            return StepOutcome(None, self.stop(TerminationReason.CANCELLED))
        if action is None:
            try:
                action = self.agent.act(before)
            except KeyboardInterrupt:
                return StepOutcome(None, self.stop(TerminationReason.CANCELLED))
            except Exception as exc:
                self._failure("agent.act", exc, TerminationReason.AGENT_ERROR)
                return StepOutcome(None, self.result)
        if not isinstance(action, Action):
            self._failure("action.validation", TypeError("action must be a v0.1 Action"),
                          TerminationReason.AGENT_ERROR)
            return StepOutcome(None, self.result)
        if self.cancel_event.is_set():
            return StepOutcome(None, self.stop(TerminationReason.CANCELLED))
        self._emit("agent", "action", action_id=action.action_id,
                   action_kind=action.kind.value, vehicle_id=action.vehicle_id)
        started = self.clock()
        try:
            execution = self.backend.execute(action)
            if execution.action_id != action.action_id:
                raise ValueError("backend execution action_id mismatch")
        except KeyboardInterrupt:
            return StepOutcome(None, self.stop(TerminationReason.CANCELLED))
        except Exception as exc:
            self._emit("runner", "component_error", stage="backend.execute", error=_error(exc).to_dict())
            execution = ExecutionResult(action.action_id, False, True, False,
                                        self.wall_clock_ns(), ContractError(
                                            "backend_exception", str(exc) or type(exc).__name__))
        elapsed = self.clock() - started
        timed_out = elapsed > action.deadline_s and execution.succeeded
        if timed_out:
            execution = ExecutionResult(action.action_id, execution.accepted, True, False,
                                        self.wall_clock_ns(), ContractError(
                                            "action_deadline_exceeded", "action exceeded deadline_s"))
        try:
            after = self.backend.observe()
            self._observation = after
            step = StepRecord(len(self._steps), before, action, execution, after)
            self._steps.append(step)
            self.store.append("trajectory.jsonl", {
                "sequence": step.sequence, "observation_before": before.to_dict(),
                "action": action.to_dict(), "execution": execution.to_dict(),
                "observation_after": after.to_dict()})
        except KeyboardInterrupt:
            return StepOutcome(None, self.stop(TerminationReason.CANCELLED))
        except Exception as exc:
            reason = TerminationReason.INITIALIZATION_ERROR if isinstance(exc, StoreError) else TerminationReason.BACKEND_ERROR
            self._failure("step.record" if isinstance(exc, StoreError) else "backend.observe_after", exc, reason)
            return StepOutcome(None, self.result)
        self._emit("backend", "execution", action_id=action.action_id,
                   succeeded=execution.succeeded,
                   error=None if execution.error is None else execution.error.to_dict())
        if self.cancel_event.is_set():
            return StepOutcome(step, self.stop(TerminationReason.CANCELLED))
        if timed_out:
            return StepOutcome(step, self.stop(self.deadline_reason))
        if not execution.succeeded:
            return StepOutcome(step, self.stop(TerminationReason.BACKEND_ERROR))
        try:
            progress = self.task.update(step)
            if not isinstance(progress, TaskProgress) or (progress.done and progress.reason is None):
                raise ValueError("task.update returned invalid progress")
            self._progress = progress
        except (Exception, KeyboardInterrupt) as exc:
            if isinstance(exc, KeyboardInterrupt):
                return StepOutcome(step, self.stop(TerminationReason.CANCELLED))
            self._failure("task.update", exc, TerminationReason.TASK_ERROR)
            return StepOutcome(step, self.result)
        try:
            metrics = dict(self.evaluator.evaluate(tuple(self._steps), self._progress))
            if any(not isinstance(key, str) or not key or isinstance(value, bool)
                   or not isinstance(value, (int, float)) or not math.isfinite(value)
                   for key, value in metrics.items()):
                raise ValueError("evaluator returned invalid metric")
            self._metrics = metrics
        except (Exception, KeyboardInterrupt) as exc:
            if isinstance(exc, KeyboardInterrupt):
                return StepOutcome(step, self.stop(TerminationReason.CANCELLED))
            self._failure("evaluator.evaluate", exc, TerminationReason.EVALUATOR_ERROR)
            return StepOutcome(step, self.result)
        reason = None
        pending = self.lifecycle.checkpoint(
            self.clock() - self._start, self._spec.time_budget_s)
        if pending is not None:
            reason = TerminationReason(pending)
        elif self._progress.done:
            reason = self._progress.reason
        elif self.max_steps is not None and len(self._steps) >= self.max_steps:
            reason = TerminationReason.TIMEOUT
        return StepOutcome(step, self.stop(reason) if reason else None)

    def stop(self, reason: TerminationReason = TerminationReason.CANCELLED) -> EpisodeResult:
        if self.state is SessionState.STOPPED and self.result is not None:
            return self.result
        if self.state is not SessionState.ACTIVE:
            raise RuntimeError("no active episode")
        self.lifecycle.end()
        if reason is TerminationReason.CANCELLED:
            self._emit("runner", "cancelled")
        try:
            cleanup = self.backend.cleanup()
            self._emit("backend", "cleanup", **cleanup.to_dict())
        except (Exception, KeyboardInterrupt) as exc:
            cleanup = CleanupResult(True, False, _error(exc))
            self._emit("backend", "cleanup_error", error=_error(exc).to_dict())
        self.lifecycle.close((("agent.close", self.agent), ("task.close", self.task),
                              ("backend.close", self.backend)), runtime=self.runtime,
                             error=lambda exc: _error(exc).to_dict())
        if self.store.failed and reason is TerminationReason.SUCCESS:
            reason = TerminationReason.INITIALIZATION_ERROR
        self._emit("runner", "termination", reason=reason.value)
        if self.store.failed and reason is TerminationReason.SUCCESS:
            reason = TerminationReason.INITIALIZATION_ERROR
            self._emit("runner", "termination", reason=reason.value)
        metrics = dict(self._metrics)
        metrics.setdefault("elapsed_s", max(0.0, self.clock() - self._start))
        self.result = EpisodeResult(self._episode_id, self._spec.task_id, reason,
                                    reason is TerminationReason.SUCCESS, self._observation,
                                    cleanup, metrics,
                                    None if self.store.failed else "trajectory.jsonl")
        self.store.finish(self.result.to_dict())
        return self.result
