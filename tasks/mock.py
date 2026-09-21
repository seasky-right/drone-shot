import math
from contracts import PlatformObservation, StepRecord, TaskProgress, TaskSpec, TerminationReason


class ReachPointTask:
    def __init__(self, target_ned: tuple[float, float, float], tolerance_m: float = 0.5) -> None:
        self.target_ned, self.tolerance_m = target_ned, tolerance_m

    def reset(self, spec: TaskSpec, initial_observation: PlatformObservation) -> None:
        del spec, initial_observation

    def update(self, step: StepRecord) -> TaskProgress:
        point = step.observation_after.position_ned
        distance = math.dist((point.north_m, point.east_m, point.down_m), self.target_ned)
        if not step.execution.succeeded:
            return TaskProgress(True, False, TerminationReason.BACKEND_ERROR)
        if distance <= self.tolerance_m:
            return TaskProgress(True, True, TerminationReason.SUCCESS)
        return TaskProgress(False, False)

    def close(self) -> None:
        return None
