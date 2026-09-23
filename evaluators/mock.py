import math
from typing import Mapping
from contracts import StepRecord, TaskProgress


class ReachPointEvaluator:
    def __init__(self, target_ned: tuple[float, float, float]) -> None:
        self.target_ned = target_ned

    def evaluate(self, steps: tuple[StepRecord, ...], progress: TaskProgress) -> Mapping[str, float]:
        point = steps[-1].observation_after.position_ned
        return {"final_distance_m": math.dist((point.north_m, point.east_m, point.down_m), self.target_ned), "task_success": float(progress.success)}

class OneStepPositionEvaluator:
    """Mock-only numeric metrics; no real task or flight claims."""

    def __init__(self, target) -> None:
        from contracts import PositionNed
        if not isinstance(target, PositionNed):
            raise TypeError("target must be PositionNed")
        self.target = target

    def evaluate(self, steps: tuple[StepRecord, ...], progress: TaskProgress) -> Mapping[str, float]:
        if not steps:
            return {"task_success": float(progress.success)}
        if self.target is None:
            raise RuntimeError("OneStepPositionEvaluator must be configured")
        before = steps[0].observation_before.position_ned
        after = steps[-1].observation_after.position_ned
        target = self.target
        return {
            "task_success": float(progress.success),
            "final_distance_m": math.dist((after.north_m, after.east_m, after.down_m),
                                          (target.north_m, target.east_m, target.down_m)),
            "path_length_m": math.dist((before.north_m, before.east_m, before.down_m),
                                       (after.north_m, after.east_m, after.down_m)),
        }
