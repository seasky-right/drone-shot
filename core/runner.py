"""One synchronous episode, independent of simulator and task implementations."""
from __future__ import annotations

import math
import platform
import time
from pathlib import Path
from typing import Callable
from uuid import uuid4

from contracts import (
    BackendConfig, CleanupResult, ContractError, EpisodeEvent, EpisodeResult,
    EventSource, ExecutionResult, PlatformContractException, PlatformObservation,
    StepRecord, TaskProgress, TaskSpec, TerminationReason, time_budget_exhausted,
)
from .recorder import Recorder


class EpisodeRunner:
    def __init__(self, backend, agent, task, evaluator, recorder: Recorder, *,
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock_ns: Callable[[], int] = time.time_ns) -> None:
        self.backend, self.agent, self.task, self.evaluator = backend, agent, task, evaluator
        self.recorder, self.clock, self.wall_clock_ns = recorder, clock, wall_clock_ns

    def run(self, spec: TaskSpec, config: BackendConfig, *, episode_id: str | None = None) -> EpisodeResult:
        episode_id = episode_id or uuid4().hex
        self.recorder.start(episode_id, spec, config, {
            "platform_contract": spec.schema, "python": platform.python_version(),
            "backend_type": config.backend_type, "agent_type": type(self.agent).__name__,
            "task_type": type(self.task).__name__, "evaluator_type": type(self.evaluator).__name__,
        })
        start = self.clock()
        steps: list[StepRecord] = []
        observation = PlatformObservation(0, config.vehicle_id, spec.home_position_ned,
                                          (0.0, 0.0, 0.0), self.wall_clock_ns(),
                                          missing_sensors={"__platform_state__": "synthetic_from_task_home; backend reset did not provide a sample"})
        reason: TerminationReason | None = None
        progress = TaskProgress(False, False)
        metrics: dict[str, float] = {}
        event_sequence = 0

        def emit(source: EventSource, kind: str, **fields: object) -> None:
            nonlocal event_sequence
            self.recorder.event(EpisodeEvent(event_sequence, source, kind, self.wall_clock_ns(), fields))
            event_sequence += 1

        def fail(stage: str, exc: Exception, termination: TerminationReason) -> None:
            nonlocal reason
            if reason is None:
                reason = termination
            error = exc.error if isinstance(exc, PlatformContractException) else ContractError(type(exc).__name__, str(exc) or type(exc).__name__)
            emit(EventSource.RUNNER, "component_error", stage=stage, error=error.to_dict())

        try:
            try:
                observation = self.backend.reset(config, spec)
                emit(EventSource.BACKEND, "reset", observation_sequence=observation.sequence)
            except Exception as exc:
                fail("backend.reset", exc, TerminationReason.INITIALIZATION_ERROR)
                emit(EventSource.RUNNER, "synthetic_final_observation", origin="task_spec.home_position_ned")
            if reason is None:
                for stage, component in (("task.reset", self.task), ("agent.reset", self.agent)):
                    try:
                        component.reset(spec, observation)
                    except Exception as exc:
                        fail(stage, exc, TerminationReason.INITIALIZATION_ERROR)
                        break
            while reason is None:
                if time_budget_exhausted(spec, self.clock() - start):
                    reason = TerminationReason.TIMEOUT
                    emit(EventSource.RUNNER, "timeout", elapsed_s=self.clock() - start)
                    break
                try:
                    before = self.backend.observe()
                    observation = before
                except Exception as exc:
                    fail("backend.observe_before", exc, TerminationReason.BACKEND_ERROR)
                    break
                try:
                    action = self.agent.act(before)
                    emit(EventSource.AGENT, "action", action_id=action.action_id, action_kind=action.kind.value)
                except Exception as exc:
                    fail("agent.act", exc, TerminationReason.AGENT_ERROR)
                    break
                action_start = self.clock()
                try:
                    execution = self.backend.execute(action)
                    if execution.action_id != action.action_id:
                        raise ValueError("backend execution action_id mismatch")
                except Exception as exc:
                    fail("backend.execute", exc, TerminationReason.BACKEND_ERROR)
                    execution = ExecutionResult(action.action_id, False, True, False, self.wall_clock_ns(),
                                                ContractError("backend_exception", str(exc) or type(exc).__name__))
                action_elapsed = self.clock() - action_start
                if action_elapsed > action.deadline_s and execution.succeeded:
                    execution = ExecutionResult(action.action_id, execution.accepted, True, False,
                                                self.wall_clock_ns(), ContractError("action_deadline_exceeded", "action exceeded deadline_s"))
                    reason = TerminationReason.BACKEND_ERROR
                try:
                    after = self.backend.observe()
                    observation = after
                except Exception as exc:
                    fail("backend.observe_after", exc, TerminationReason.BACKEND_ERROR)
                    break
                try:
                    step = StepRecord(len(steps), before, action, execution, after)
                except Exception as exc:
                    fail("step.validation", exc, TerminationReason.BACKEND_ERROR)
                    break
                steps.append(step)
                self.recorder.step(step)
                emit(EventSource.BACKEND, "execution", action_id=action.action_id,
                     succeeded=execution.succeeded,
                     error=None if execution.error is None else execution.error.to_dict())
                if reason is not None:
                    break
                try:
                    progress = self.task.update(step)
                    if not isinstance(progress, TaskProgress):
                        raise TypeError("task.update must return TaskProgress")
                    if progress.done and progress.reason is None:
                        raise ValueError("done task progress requires a reason")
                except Exception as exc:
                    fail("task.update", exc, TerminationReason.TASK_ERROR)
                    break
                try:
                    metrics = dict(self.evaluator.evaluate(tuple(steps), progress))
                    for metric_name, metric_value in metrics.items():
                        if not isinstance(metric_name, str) or not metric_name or isinstance(metric_value, bool) or not isinstance(metric_value, (int, float)) or not math.isfinite(metric_value):
                            raise ValueError("evaluator returned invalid metric")
                except Exception as exc:
                    fail("evaluator.evaluate", exc, TerminationReason.EVALUATOR_ERROR)
                    break
                if not execution.succeeded:
                    reason = TerminationReason.BACKEND_ERROR
                elif time_budget_exhausted(spec, self.clock() - start):
                    reason = TerminationReason.TIMEOUT
                elif progress.done:
                    reason = progress.reason or TerminationReason.TASK_FAILED
                if reason is not None:
                    emit(EventSource.RUNNER, "termination", reason=reason.value,
                         observation_sequence=after.sequence)
        except KeyboardInterrupt:
            reason = TerminationReason.CANCELLED
            emit(EventSource.RUNNER, "cancelled")
        finally:
            try:
                cleanup = self.backend.cleanup()
                emit(EventSource.BACKEND, "cleanup", **cleanup.to_dict())
            except Exception as exc:
                error = exc.error if isinstance(exc, PlatformContractException) else ContractError(type(exc).__name__, str(exc) or type(exc).__name__)
                cleanup = CleanupResult(True, False, error)
                emit(EventSource.BACKEND, "cleanup_error", error=error.to_dict())
            for stage, component in (("agent.close", self.agent), ("task.close", self.task), ("backend.close", self.backend)):
                try:
                    component.close()
                except Exception as exc:
                    emit(EventSource.RUNNER, "close_error", stage=stage, error=str(exc))
        if reason is None:
            reason = TerminationReason.TASK_FAILED
        if self.recorder.failed and reason is TerminationReason.SUCCESS:
            reason = TerminationReason.INITIALIZATION_ERROR
        metrics.setdefault("elapsed_s", max(0.0, self.clock() - start))
        result = EpisodeResult(episode_id, spec.task_id, reason,
                               reason is TerminationReason.SUCCESS, observation,
                               cleanup, metrics, None if self.recorder.failed else "trajectory.jsonl")
        self.recorder.finish(result)
        return result
