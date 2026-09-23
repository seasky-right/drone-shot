"""ReachPoint v0.1 task rules shared by the Task and Evaluator."""

from __future__ import annotations

import math
from dataclasses import dataclass

from contracts import (
    ContractValidationError, PlatformObservation, PositionNed, StepRecord,
    TaskProgress, TaskSpec, TerminationReason,
)


def distance_m(a: PositionNed, b: PositionNed) -> float:
    return math.dist((a.north_m, a.east_m, a.down_m),
                     (b.north_m, b.east_m, b.down_m))


@dataclass(frozen=True)
class ReachPointRules:
    target_position_ned: PositionNed
    tolerance_m: float
    require_return_home: bool
    require_landing: bool
    collision_policy: str

    @classmethod
    def from_spec(cls, spec: TaskSpec) -> "ReachPointRules":
        if spec.task_type != "reach_point":
            raise ContractValidationError("ReachPoint requires task_type=reach_point")
        p = spec.parameters
        required = {"target_position_ned", "tolerance_m", "require_return_home",
                    "require_landing", "collision_policy"}
        if set(p) != required:
            raise ContractValidationError(
                f"ReachPoint parameters must be exactly {', '.join(sorted(required))}"
            )
        target = PositionNed.from_dict(p["target_position_ned"])
        tolerance = p["tolerance_m"]
        if (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float))
                or not math.isfinite(tolerance) or tolerance <= 0):
            raise ContractValidationError("tolerance_m must be finite and positive")
        for key in ("require_return_home", "require_landing"):
            if not isinstance(p[key], bool):
                raise ContractValidationError(f"{key} must be boolean")
        if p["collision_policy"] not in ("zero", "disqualify"):
            raise ContractValidationError("collision_policy must be zero or disqualify")
        return cls(target, float(tolerance), p["require_return_home"],
                   p["require_landing"], p["collision_policy"])


class ReachPointTask:
    """Task completion uses the observation after an action, never command status."""

    def __init__(self) -> None:
        self.rules: ReachPointRules | None = None

    def reset(self, spec: TaskSpec, initial_observation: PlatformObservation) -> None:
        del initial_observation
        self.rules = ReachPointRules.from_spec(spec)

    def update(self, step: StepRecord) -> TaskProgress:
        if self.rules is None:
            raise RuntimeError("ReachPointTask.reset must be called first")
        if not step.execution.succeeded:
            return TaskProgress(True, False, TerminationReason.BACKEND_ERROR)
        reached = distance_m(step.observation_after.position_ned,
                             self.rules.target_position_ned) <= self.rules.tolerance_m
        if reached:
            return TaskProgress(True, True, TerminationReason.SUCCESS)
        return TaskProgress(False, False)

    def close(self) -> None:
        self.rules = None
