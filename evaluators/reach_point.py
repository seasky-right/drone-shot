"""Pure ReachPoint v0.1 scoring from recorded observations and runner elapsed time."""

from __future__ import annotations

import math
from dataclasses import dataclass

from contracts import (
    CleanupResult, ContractValidationError, EpisodeEvent, PlatformObservation,
    StepRecord, TaskProgress, TaskSpec, TerminationReason,
)
from tasks.reach_point import ReachPointRules, distance_m

EVALUATOR_VERSION = "reach_point/v0.1"


@dataclass(frozen=True)
class ReachPointEvaluation:
    metrics: dict[str, float | bool | None]
    goal_score: float
    time_score: float
    score: float
    rank_eligible: bool
    safety_status: str
    evaluator_version: str = EVALUATOR_VERSION


def _elapsed(value: object) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ContractValidationError("step elapsed time must be finite and non-negative")
    return float(value)


class ReachPointEvaluator:
    """C1 evaluator. Runner must supply monotonic elapsed seconds for every step."""

    def __init__(self, spec: TaskSpec) -> None:
        self.spec = spec
        self.rules = ReachPointRules.from_spec(spec)

    def evaluate(
        self, steps: tuple[StepRecord, ...], progress: TaskProgress,
    ) -> dict[str, float]:
        """G0 Evaluator protocol: FR-EVL-01 metrics without runner-only data."""
        if not steps:
            raise ContractValidationError("at least one post-action observation is required")
        positions = [steps[0].observation_before.position_ned]
        previous = steps[0].observation_before
        for step in steps:
            if step.observation_before != previous:
                raise ContractValidationError("steps must form a continuous observation chain")
            positions.append(step.observation_after.position_ned)
            previous = step.observation_after
        distances = [distance_m(p, self.rules.target_position_ned) for p in positions]
        reached = any(d <= self.rules.tolerance_m for d in distances[1:])
        if progress.success and not reached:
            raise ContractValidationError("Task reported success without a post-action goal observation")
        return {
            "goal_reached": float(reached),
            "goal_score": 70.0 if reached and progress.success else 0.0,
            "final_distance_m": distances[-1],
            "min_distance_m": min(distances),
            "path_length_m": sum(distance_m(a, b) for a, b in zip(positions, positions[1:])),
        }

    def evaluate_episode(
        self, initial_observation: PlatformObservation,
        steps: tuple[StepRecord, ...], step_elapsed_s: tuple[float, ...],
        progress: TaskProgress, termination_reason: TerminationReason,
        events: tuple[EpisodeEvent, ...] | None, cleanup: CleanupResult,
    ) -> ReachPointEvaluation:
        if len(steps) != len(step_elapsed_s):
            raise ContractValidationError("each step requires monotonic elapsed seconds")
        if not isinstance(termination_reason, TerminationReason):
            raise ContractValidationError("termination_reason must be a TerminationReason")
        if progress.success and termination_reason is not TerminationReason.SUCCESS:
            raise ContractValidationError("successful TaskProgress conflicts with termination")
        if termination_reason is TerminationReason.SUCCESS and not progress.success:
            raise ContractValidationError("success termination requires successful TaskProgress")
        times = tuple(_elapsed(t) for t in step_elapsed_s)
        if any(b < a for a, b in zip(times, times[1:])):
            raise ContractValidationError("step elapsed times must be monotonic")
        previous = initial_observation
        positions = [previous.position_ned]
        for step in steps:
            if step.observation_before != previous:
                raise ContractValidationError("steps must form a continuous observation chain")
            if step.observation_after.vehicle_id != initial_observation.vehicle_id:
                raise ContractValidationError("steps must belong to the initial vehicle")
            positions.append(step.observation_after.position_ned)
            previous = step.observation_after
        distances = [distance_m(p, self.rules.target_position_ned) for p in positions]
        hit_index = next((i for i, d in enumerate(distances[1:]) if d <= self.rules.tolerance_m), None)
        reached = hit_index is not None
        if progress.success and not reached:
            raise ContractValidationError("Task reported success without a post-action goal observation")
        completion = times[hit_index] if hit_index is not None else None
        path_length = sum(distance_m(a, b) for a, b in zip(positions, positions[1:]))

        event_kinds = [e.kind for e in events] if events is not None else []
        collision_count = event_kinds.count("collision") if events is not None else None
        boundary_count = event_kinds.count("boundary_violation") if events is not None else None
        returned = "return_home_reached" in event_kinds if events is not None else None
        landed = "landed" in event_kinds if events is not None else None
        collision_or_boundary = bool(collision_count or boundary_count)
        return_failed = self.rules.require_return_home and not returned
        landing_failed = self.rules.require_landing and not landed
        cleanup_failed = (self.rules.require_return_home or self.rules.require_landing) and not cleanup.succeeded
        if events is None:
            safety_status = "unavailable"
        elif collision_or_boundary or return_failed or landing_failed or cleanup_failed:
            safety_status = "failed"
        else:
            safety_status = "passed"

        goal_score = 70.0 if reached and progress.success else 0.0
        valid_success = (termination_reason is TerminationReason.SUCCESS and reached
                         and completion is not None and completion <= self.spec.time_budget_s)
        time_score = (30.0 * max(0.0, 1.0 - completion / self.spec.time_budget_s)
                      if valid_success and completion is not None else 0.0)
        zero_policy_failure = (collision_or_boundary and self.rules.collision_policy == "zero"
                               and not (return_failed or landing_failed or cleanup_failed))
        rank_eligible = valid_success and (safety_status == "passed" or zero_policy_failure)
        score = goal_score + time_score if safety_status == "passed" and valid_success else 0.0
        metrics: dict[str, float | bool | None] = {
            "goal_reached": reached,
            "final_distance_m": distances[-1],
            "min_distance_m": min(distances),
            "completion_time_s": completion,
            "path_length_m": path_length,
            "collision_count": collision_count,
            "boundary_violation_count": boundary_count,
            "return_home_success": returned if self.rules.require_return_home else None,
            "landed_success": landed if self.rules.require_landing else None,
        }
        return ReachPointEvaluation(metrics, goal_score, time_score, score,
                                    rank_eligible, safety_status)
