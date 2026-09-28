"""No-simulator v0.2 multi-vehicle episode engine.

This is an additive contract consumer. It does not change the v0.1 Runner.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import time
from typing import Callable, Mapping, Protocol, Sequence
from uuid import uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from contracts.data_v02 import (
    ActionChannel, ActionV02, AgentBindingV02, CapabilitySetV02, EpisodeSnapshotV02,
    PlatformObservationV02, ScenarioSpecV02,
)
from contracts.model import ContractValidationError
from .episode_lifecycle import EpisodeLifecycle


class PreflightError(ContractValidationError):
    """A declared combination cannot run with the available resources."""


class EpisodeStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_FAILURE = "partial_failure"
    STEP_LIMIT = "step_limit"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ActionOutcome:
    action_id: str
    vehicle_id: str
    succeeded: bool
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.action_id or not self.vehicle_id:
            raise ContractValidationError("outcome must identify action and vehicle")
        if not isinstance(self.succeeded, bool) or (self.succeeded and self.error is not None):
            raise ContractValidationError("invalid action outcome")
        if not self.succeeded and not self.error:
            raise ContractValidationError("failed action requires an error")


@dataclass(frozen=True)
class MultiVehicleStep:
    before: EpisodeSnapshotV02
    actions: tuple[ActionV02, ...]
    outcomes: tuple[ActionOutcome, ...]
    after: EpisodeSnapshotV02


@dataclass(frozen=True)
class MultiVehicleResult:
    status: EpisodeStatus
    steps: tuple[MultiVehicleStep, ...]
    final_snapshot: EpisodeSnapshotV02
    cleanup_errors: tuple[str, ...] = ()


class MultiVehicleBackend(Protocol):
    def reset(self, scenario: ScenarioSpecV02, vehicle_ids: tuple[str, ...]) -> EpisodeSnapshotV02: ...
    def execute(self, action: ActionV02) -> ActionOutcome: ...
    def observe(self) -> EpisodeSnapshotV02: ...
    def close(self) -> None: ...


class CentralAgent(Protocol):
    def act(self, snapshot: EpisodeSnapshotV02) -> Sequence[ActionV02]: ...


class VehicleAgent(Protocol):
    def act(self, observation: PlatformObservationV02) -> ActionV02: ...


class MultiVehicleTask(Protocol):
    def complete(self, snapshot: EpisodeSnapshotV02, truth: Mapping[str, object]) -> bool: ...


class MultiVehicleEpisode:
    def __init__(self, backend: MultiVehicleBackend, capability: CapabilitySetV02,
                 scenario: ScenarioSpecV02, agents: Mapping[str, object],
                 bindings: Sequence[AgentBindingV02], task: MultiVehicleTask,
                 *, vehicle_ids: Sequence[str], required_sensor_resources: Mapping[str, str] | None = None,
                 defer_sensor_resource_check: bool = False,
                 on_confirmed_capabilities: Callable[[CapabilitySetV02], None] | None = None,
                 action_schemas: Mapping[str, str] | None = None,
                 payload_schemas: Mapping[str, Mapping[str, object]] | None = None,
                 required_action_kinds: Sequence[str] = (), truth: Mapping[str, object] | None = None,
                 action_handlers: Mapping[ActionChannel, Callable[[ActionV02], ActionOutcome]] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 lifecycle: EpisodeLifecycle | None = None, runtime=None,
                 episode_id: str | None = None,
                 cleanup_components: Sequence[tuple[str, object]] = ()) -> None:
        self.backend, self.capability, self.scenario = backend, capability, scenario
        self.agents, self.bindings, self.task = dict(agents), tuple(bindings), task
        self.vehicle_ids = tuple(vehicle_ids)
        self.required_sensor_resources = dict(required_sensor_resources or {})
        self.defer_sensor_resource_check = defer_sensor_resource_check
        self.on_confirmed_capabilities = on_confirmed_capabilities
        self.action_schemas = dict(action_schemas or {})
        self.payload_schemas = dict(payload_schemas or {})
        self.required_action_kinds = tuple(required_action_kinds)
        self.truth = dict(truth or {})
        self.action_handlers = dict(action_handlers or {})
        self.clock = clock
        self.lifecycle = lifecycle or EpisodeLifecycle(lambda event: None, clock=clock)
        self.runtime = runtime
        self.episode_id = episode_id or uuid4().hex
        self.cleanup_components = tuple(cleanup_components)
        self._active = False
        self._closed = False
        self.result: MultiVehicleResult | None = None
        self._preflight()

    def request_cancel(self) -> None:
        self.lifecycle.request_cancel()

    def _record_error(self, stage: str, exc: BaseException) -> None:
        try:
            self.lifecycle.emit("runner", "component_error", stage=stage,
                                error=f"{type(exc).__name__}: {exc}")
        except Exception:
            pass

    def _is_cancelled(self) -> bool:
        return self.lifecycle.cancel_event.is_set() or self._cancelled()

    def _checkpoint(self) -> str | None:
        if self._is_cancelled():
            return "cancelled"
        budget = self._time_budget_s if self._time_budget_s is not None else math.inf
        return self.lifecycle.checkpoint(self.clock() - self._start, budget)

    def _preflight(self) -> None:
        if not self.vehicle_ids or len(set(self.vehicle_ids)) != len(self.vehicle_ids):
            raise PreflightError("vehicle IDs must be non-empty and unique")
        if len(self.vehicle_ids) > self.capability.max_vehicles:
            raise PreflightError("backend vehicle capacity is insufficient")
        if not {"load", "reset"}.issubset(self.capability.scenario_operations):
            raise PreflightError("backend cannot load and reset the scenario")
        if not self.defer_sensor_resource_check:
            for resource_id, kind in self.required_sensor_resources.items():
                if self.capability.sensor_resources.get(resource_id) != kind:
                    raise PreflightError(f"required sensor resource unavailable: {resource_id}")
        if not set(self.required_action_kinds).issubset(self.capability.action_kinds):
            raise PreflightError("required action kind unsupported")
        if not set(self.required_action_kinds).issubset(self.action_schemas):
            raise PreflightError("required action schema missing")
        if not set(self.required_action_kinds).issubset(self.payload_schemas):
            raise PreflightError("required action payload JSON schema missing")
        for kind, schema in self.payload_schemas.items():
            if kind not in self.action_schemas:
                raise PreflightError(f"payload schema has no registered action: {kind}")
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                raise PreflightError(f"invalid payload schema for {kind}: {exc.message}") from exc
        if self.truth and (self.scenario.truth_access != "task_evaluator" or not self.capability.truth_access):
            raise PreflightError("truth channel unavailable")
        covered: list[str] = []
        for binding in self.bindings:
            if binding.agent_id not in self.agents:
                raise PreflightError(f"agent not supplied: {binding.agent_id}")
            covered.extend(binding.vehicle_ids)
        if sorted(covered) != sorted(self.vehicle_ids):
            raise PreflightError("each vehicle must have exactly one agent binding")

    def _actions(self, snapshot: EpisodeSnapshotV02) -> tuple[ActionV02, ...]:
        actions: list[ActionV02] = []
        for binding in self.bindings:
            if any(vehicle not in snapshot.observations for vehicle in binding.vehicle_ids):
                raise ContractValidationError("bound vehicle observation missing")
            agent = self.agents[binding.agent_id]
            if len(binding.vehicle_ids) == 1:
                produced = (agent.act(snapshot.observations[binding.vehicle_ids[0]]),)
            else:
                produced = tuple(agent.act(snapshot))
            for action in produced:
                if not isinstance(action, ActionV02) or action.vehicle_id not in binding.vehicle_ids:
                    raise ContractValidationError("agent returned action for another vehicle")
                if action.kind not in self.capability.action_kinds:
                    raise ContractValidationError(f"unsupported action kind: {action.kind}")
                if self.action_schemas.get(action.kind) != action.payload_schema:
                    raise ContractValidationError(f"unregistered action payload schema: {action.kind}")
                if action.channel is not ActionChannel.CONTROL and action.channel not in self.action_handlers:
                    raise ContractValidationError(
                        f"no {action.channel.value} handler for action {action.kind}")
                if action.kind not in self.payload_schemas:
                    raise ContractValidationError(
                        f"unregistered action payload JSON schema: {action.kind}")
                try:
                    Draft202012Validator(self.payload_schemas[action.kind]).validate(action.payload)
                except ValidationError as exc:
                    raise ContractValidationError(
                        f"invalid {action.kind} payload: {exc.message}") from exc
                actions.append(action)
        if len({action.action_id for action in actions}) != len(actions):
            raise ContractValidationError("duplicate action ID in one step")
        return tuple(actions)

    def reset(self, *, max_steps: int, time_budget_s: float | None = None,
              cancelled: Callable[[], bool] | None = None) -> EpisodeSnapshotV02:
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
            raise ValueError("max_steps must be positive")
        if time_budget_s is not None and (isinstance(time_budget_s, bool)
                                          or not isinstance(time_budget_s, (int, float))
                                          or not math.isfinite(time_budget_s)
                                          or time_budget_s <= 0):
            raise ValueError("time_budget_s must be finite and positive")
        if self._active or self._closed:
            raise RuntimeError("this episode cannot be reset again")
        self._max_steps = max_steps
        self._time_budget_s = time_budget_s
        self._cancelled = cancelled or (lambda: False)
        self._start = self.clock()
        self._steps: list[MultiVehicleStep] = []
        self.lifecycle.begin()
        stage = "runtime.acquire"
        try:
            self.lifecycle.acquire(self.runtime, self.episode_id, {
                "scenario": self.scenario.to_dict(), "vehicle_ids": list(self.vehicle_ids)})
            stage = "backend.reset"
            self._snapshot = self.backend.reset(self.scenario, self.vehicle_ids)
            self._check_snapshot(self._snapshot)
            if self.defer_sensor_resource_check:
                stage = "sensor_resources.confirm"
                confirmed = self.backend.capabilities()
                if not isinstance(confirmed, CapabilitySetV02):
                    raise ContractValidationError("backend.capabilities must return CapabilitySetV02")
                for resource_id, kind in self.required_sensor_resources.items():
                    if confirmed.sensor_resources.get(resource_id) != kind:
                        raise PreflightError(f"required sensor resource unavailable after reset: {resource_id}")
                if self.on_confirmed_capabilities is not None:
                    self.on_confirmed_capabilities(confirmed)
            self._active = True
            self.lifecycle.emit("backend", "reset", observation_sequence=self._snapshot.sequence)
            if self._snapshot.missing_vehicles:
                self.stop(EpisodeStatus.PARTIAL_FAILURE)
            return self._snapshot
        except BaseException as exc:
            self._record_error(stage, exc)
            try:
                self._close()
            finally:
                self.lifecycle.end()
            raise

    def step(self) -> MultiVehicleResult | None:
        if not self._active:
            raise RuntimeError("reset an episode before stepping")
        if self._checkpoint() == "cancelled":
            return self.stop(EpisodeStatus.CANCELLED)
        if self._checkpoint() == "timeout":
            return self.stop(EpisodeStatus.TIMEOUT)
        try:
            before = self._snapshot
            actions = self._actions(before)
            signal = self._checkpoint()
            if signal == "cancelled":
                return self.stop(EpisodeStatus.CANCELLED)
            if signal == "timeout":
                return self.stop(EpisodeStatus.TIMEOUT)
            for action in actions:
                self.lifecycle.emit("agent", "action", action_id=action.action_id,
                                    vehicle_id=action.vehicle_id, action_kind=action.kind,
                                    channel=action.channel.value)
            outcomes: list[ActionOutcome] = []
            action_timed_out = False
            cancelled_after_action = False
            for index, action in enumerate(actions):
                started = self.clock()
                try:
                    execute = (self.backend.execute if action.channel is ActionChannel.CONTROL
                               else self.action_handlers[action.channel])
                    outcome = execute(action)
                    if outcome.action_id != action.action_id or outcome.vehicle_id != action.vehicle_id:
                        raise ContractValidationError("backend outcome ID mismatch")
                except Exception as exc:
                    self.lifecycle.emit("runner", "component_error", stage="action.execute",
                                        action_id=action.action_id, vehicle_id=action.vehicle_id,
                                        error=f"{type(exc).__name__}: {exc}")
                    outcome = ActionOutcome(action.action_id, action.vehicle_id, False,
                                            f"{type(exc).__name__}: {exc}")
                if self.clock() - started > action.deadline_s:
                    outcome = ActionOutcome(action.action_id, action.vehicle_id, False,
                                            "action_deadline_exceeded")
                    action_timed_out = True
                outcomes.append(outcome)
                self.lifecycle.emit("backend", "execution", action_id=action.action_id,
                                    vehicle_id=action.vehicle_id, succeeded=outcome.succeeded,
                                    error=outcome.error)
                signal = self._checkpoint()
                budget_exhausted = signal == "timeout"
                cancelled_after_action = signal == "cancelled"
                if action_timed_out or budget_exhausted or cancelled_after_action:
                    skipped = ("skipped_after_cancel" if cancelled_after_action
                               else "skipped_after_timeout")
                    outcomes.extend(ActionOutcome(pending.action_id, pending.vehicle_id,
                                                  False, skipped)
                                    for pending in actions[index + 1:])
                    break
            after = self.backend.observe()
            self._check_snapshot(after)
            if after.sequence <= before.sequence:
                raise ContractValidationError("snapshot sequence did not advance")
            self._steps.append(MultiVehicleStep(before, actions, tuple(outcomes), after))
            self._snapshot = after
            signal = self._checkpoint()
            if cancelled_after_action or signal == "cancelled":
                return self.stop(EpisodeStatus.CANCELLED)
            if action_timed_out or signal == "timeout":
                return self.stop(EpisodeStatus.TIMEOUT)
            if any(not outcome.succeeded for outcome in outcomes) or after.missing_vehicles:
                return self.stop(EpisodeStatus.PARTIAL_FAILURE)
            if self.task.complete(after, self.truth):
                return self.stop(EpisodeStatus.SUCCESS)
            if len(self._steps) >= self._max_steps:
                return self.stop(EpisodeStatus.STEP_LIMIT)
            return None
        except KeyboardInterrupt:
            return self.stop(EpisodeStatus.CANCELLED)
        except BaseException as exc:
            self._record_error("episode.step", exc)
            self._active = False
            try:
                self._close()
            finally:
                self.lifecycle.end()
            raise

    def stop(self, status: EpisodeStatus = EpisodeStatus.CANCELLED) -> MultiVehicleResult:
        if self.result is not None:
            return self.result
        if not self._active:
            raise RuntimeError("no active episode")
        self._active = False
        errors = self._close()
        self.result = MultiVehicleResult(status, tuple(self._steps), self._snapshot, errors)
        try:
            self.lifecycle.emit("runner", "termination", reason=status.value,
                                cleanup_errors=list(errors))
        finally:
            self.lifecycle.end()
        return self.result

    def run(self, *, max_steps: int, time_budget_s: float | None = None,
            cancelled: Callable[[], bool] | None = None) -> MultiVehicleResult:
        self.reset(max_steps=max_steps, time_budget_s=time_budget_s, cancelled=cancelled)
        while self.result is None:
            self.step()
        return self.result

    def _close(self) -> tuple[str, ...]:
        if self._closed:
            return ()
        self._closed = True
        components = (("backend.close", self.backend), ("task.close", self.task),
                      *((f"agent:{name}.close", agent) for name, agent in self.agents.items()),
                      *self.cleanup_components)
        return self.lifecycle.close(((name, component) for name, component in components
                                     if getattr(component, "close", None) is not None),
                                    runtime=self.runtime)

    def _check_snapshot(self, snapshot: EpisodeSnapshotV02) -> None:
        if not isinstance(snapshot, EpisodeSnapshotV02):
            raise ContractValidationError("backend must return EpisodeSnapshotV02")
        if set(snapshot.observations) | set(snapshot.missing_vehicles) != set(self.vehicle_ids):
            raise ContractValidationError("snapshot vehicle IDs differ from configured vehicles")
