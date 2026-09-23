"""Minimal deterministic ReachPoint metrics; no unavailable safety data is invented."""
from __future__ import annotations

from typing import Mapping

from contracts import PositionNed, StepRecord, TaskProgress, TaskSpec
from tasks.reachpoint import ReachPointRules, distance_m


class ReachPointEvaluator:
    def __init__(self, target: PositionNed) -> None:
        self.target = target

    @classmethod
    def from_spec(cls, spec: TaskSpec) -> "ReachPointEvaluator":
        return cls(ReachPointRules.from_spec(spec).target)

    def evaluate(self, steps: tuple[StepRecord, ...], progress: TaskProgress) -> Mapping[str, float]:
        metrics: dict[str, float] = {"task_success": float(progress.success)}
        if not steps:
            return metrics
        positions = [steps[0].observation_before.position_ned]
        positions.extend(step.observation_after.position_ned for step in steps)
        metrics["final_distance_m"] = distance_m(positions[-1], self.target)
        metrics["minimum_distance_m"] = min(distance_m(point, self.target) for point in positions)
        metrics["path_length_m"] = sum(distance_m(before, after) for before, after in zip(positions, positions[1:]))
        return metrics
