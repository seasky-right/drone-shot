import math
from typing import Mapping
from contracts import StepRecord, TaskProgress


class ReachPointEvaluator:
    def __init__(self, target_ned: tuple[float, float, float]) -> None:
        self.target_ned = target_ned

    def evaluate(self, steps: tuple[StepRecord, ...], progress: TaskProgress) -> Mapping[str, float]:
        point = steps[-1].observation_after.position_ned
        return {"final_distance_m": math.dist((point.north_m, point.east_m, point.down_m), self.target_ned), "task_success": float(progress.success)}
