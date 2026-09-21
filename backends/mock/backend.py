"""Deterministic no-physics backend for P1 consumer and contract tests."""
from __future__ import annotations

import time

from contracts import Action, ActionKind, BackendConfig, CleanupResult, ContractError, ExecutionResult, PlatformContractException, PlatformObservation, PositionNed, TaskSpec


class MockBackend:
    """Only move_to and hover are supported; this is neither dynamics nor sensor simulation."""
    def __init__(self, fail_next_action: bool = False) -> None:
        self._config: BackendConfig | None = None
        self._position: PositionNed | None = None
        self._sequence = 0
        self._fail_next_action = fail_next_action

    def reset(self, config: BackendConfig, task: TaskSpec) -> PlatformObservation:
        if config.backend_type != "mock":
            raise ValueError("MockBackend requires backend_type=mock")
        self._config, self._position, self._sequence = config, task.home_position_ned, 0
        return self.observe()

    def observe(self) -> PlatformObservation:
        if self._config is None or self._position is None:
            raise PlatformContractException(ContractError("not_connected", "MockBackend has no active session"))
        return PlatformObservation(self._sequence, self._config.vehicle_id, self._position, (0.0, 0.0, 0.0), time.time_ns())

    def execute(self, action: Action) -> ExecutionResult:
        if self._config is None:
            return ExecutionResult(action.action_id, False, True, False, time.time_ns(), ContractError("not_connected", "MockBackend has no active session"))
        now = time.time_ns()
        if action.vehicle_id != self._config.vehicle_id:
            return ExecutionResult(action.action_id, False, True, False, now, ContractError("invalid_argument", "action vehicle_id does not match backend vehicle"))
        if self._fail_next_action:
            self._fail_next_action = False
            # The post-action observation is a newly sampled record even when
            # the state itself is unchanged by a failed command.
            self._sequence += 1
            return ExecutionResult(action.action_id, True, True, False, now, ContractError("backend_error", "scripted MockBackend failure"))
        if action.kind is ActionKind.MOVE_TO:
            self._position = action.target_position_ned
        elif action.kind is not ActionKind.HOVER:
            return ExecutionResult(action.action_id, False, True, False, now, ContractError("not_supported", f"MockBackend does not support {action.kind.value}"))
        self._sequence += 1
        return ExecutionResult(action.action_id, True, True, True, now)

    def cleanup(self) -> CleanupResult:
        return CleanupResult(True, True)

    def close(self) -> None:
        """Release the session only; cleanup remains an explicit separate operation."""
        self._config, self._position, self._sequence = None, None, 0
