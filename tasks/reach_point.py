"""Formal ReachPoint task and shared evaluation rules for contract v0.1."""
from __future__ import annotations

import math
from dataclasses import dataclass
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


def distance_m(a: PositionNed, b: PositionNed) -> float:
    """Return Euclidean distance between two NED positions in metres."""
    return math.dist(
        (a.north_m, a.east_m, a.down_m),
        (b.north_m, b.east_m, b.down_m),
    )


@dataclass(frozen=True)
class ReachPointRules:
    """Validated ReachPoint rules shared by task, agents, and evaluator.

    Safety fields have defaults so the P2 runtime configurations and the richer
    offline evaluation pack use the same task module.
    """

    target_position_ned: PositionNed
    tolerance_m: float = 0.5
    require_return_home: bool = False
    require_landing: bool = False
    collision_policy: str = "zero"

    @classmethod
    def from_spec(cls, spec: TaskSpec) -> "ReachPointRules":
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

        target = PositionNed.from_dict(params["target_position_ned"])
        tolerance = params.get("tolerance_m", 0.5)
        if (
            isinstance(tolerance, bool)
            or not isinstance(tolerance, (int, float))
            or not math.isfinite(tolerance)
            or tolerance <= 0
        ):
            raise ContractValidationError(
                "tolerance_m must be a positive finite number"
            )

        require_return_home = params.get("require_return_home", False)
        require_landing = params.get("require_landing", False)
        for key, value in (
            ("require_return_home", require_return_home),
            ("require_landing", require_landing),
        ):
            if not isinstance(value, bool):
                raise ContractValidationError(f"{key} must be boolean")

        collision_policy = params.get("collision_policy", "zero")
        if collision_policy not in ("zero", "disqualify"):
            raise ContractValidationError(
                "collision_policy must be zero or disqualify"
            )

        return cls(
            target_position_ned=target,
            tolerance_m=float(tolerance),
            require_return_home=require_return_home,
            require_landing=require_landing,
            collision_policy=collision_policy,
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

        rules = ReachPointRules.from_spec(spec)

        vehicle_id = initial_observation.vehicle_id

        # Commit validated state atomically
        self._target_position_ned = rules.target_position_ned
        self._tolerance_m = rules.tolerance_m
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
        distance = distance_m(after_pos, tgt)

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
