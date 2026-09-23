"""Formal ReachPointTask under platform contract v0.1."""
from __future__ import annotations

import math
from typing import Mapping

from contracts import (
    ContractValidationError,
    PlatformObservation,
    PositionNed,
    StepRecord,
    TaskProgress,
    TaskSpec,
    TerminationReason,
)


class ReachPointTask:
    """Task that terminates when drone reaches target NED coordinates within tolerance."""

    def __init__(self) -> None:
        self._target_position_ned: PositionNed | None = None
        self._tolerance_m: float = 0.5
        self._vehicle_id: str | None = None

    def reset(self, spec: TaskSpec, initial_observation: PlatformObservation) -> None:
        """Initialize the task from TaskSpec and initial observation.

        Guarantees atomicity: if reset fails for any reason, existing state is cleared
        and the task remains in an uninitialized state.
        """
        # Clear existing state immediately to prevent hybrid state on validation failure
        self.close()

        if spec.task_type != "reach_point":
            raise ContractValidationError(
                f"task_type must be reach_point, got '{spec.task_type}'"
            )

        params = spec.parameters
        if not isinstance(params, Mapping):
            raise ContractValidationError("task parameters must be an object")

        if "target_position_ned" not in params:
            raise ContractValidationError(
                "task parameters missing required 'target_position_ned'"
            )

        target_position_ned = PositionNed.from_dict(params["target_position_ned"])

        if "tolerance_m" in params:
            tol = params["tolerance_m"]
            if (
                isinstance(tol, bool)
                or not isinstance(tol, (int, float))
                or not math.isfinite(tol)
                or tol <= 0
            ):
                raise ContractValidationError(
                    "tolerance_m must be a positive finite number"
                )
            tolerance_m = float(tol)
        else:
            tolerance_m = 0.5

        vehicle_id = initial_observation.vehicle_id

        # Commit validated state atomically
        self._target_position_ned = target_position_ned
        self._tolerance_m = tolerance_m
        self._vehicle_id = vehicle_id

    def update(self, step: StepRecord) -> TaskProgress:
        """Update task progress based on step record."""
        if self._vehicle_id is None or self._target_position_ned is None:
            raise RuntimeError("task must be reset before calling update()")

        if (
            step.observation_before.vehicle_id != self._vehicle_id
            or step.action.vehicle_id != self._vehicle_id
            or step.observation_after.vehicle_id != self._vehicle_id
        ):
            raise ContractValidationError(
                f"step vehicle_id does not match task vehicle_id '{self._vehicle_id}' "
                f"(before: '{step.observation_before.vehicle_id}', "
                f"action: '{step.action.vehicle_id}', "
                f"after: '{step.observation_after.vehicle_id}')"
            )

        if not step.execution.succeeded:
            return TaskProgress(
                done=True,
                success=False,
                reason=TerminationReason.BACKEND_ERROR,
            )

        after_pos = step.observation_after.position_ned
        tgt = self._target_position_ned
        distance = math.dist(
            (after_pos.north_m, after_pos.east_m, after_pos.down_m),
            (tgt.north_m, tgt.east_m, tgt.down_m),
        )

        if distance <= self._tolerance_m:
            return TaskProgress(
                done=True,
                success=True,
                reason=TerminationReason.SUCCESS,
            )

        return TaskProgress(
            done=False,
            success=False,
            reason=None,
        )

    def close(self) -> None:
        """Release episode state."""
        self._target_position_ned = None
        self._vehicle_id = None
