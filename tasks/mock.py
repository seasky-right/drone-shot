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

class OneStepPositionTask:
    """Mock-only one-action outcome so batch runs do not depend on wall time."""

    def __init__(self) -> None:
        self.target = None
        self.tolerance_m = 0.5

    def reset(self, spec: TaskSpec, initial_observation: PlatformObservation) -> None:
        from contracts import ContractValidationError, PositionNed
        del initial_observation
        self.target = PositionNed.from_dict(spec.parameters["target_position_ned"])
        tolerance = spec.parameters.get("tolerance_m", 0.5)
        if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance < 0:
            raise ContractValidationError("mock tolerance_m must be finite and nonnegative")
        self.tolerance_m = float(tolerance)

    def update(self, step: StepRecord) -> TaskProgress:
        if self.target is None:
            raise RuntimeError("OneStepPositionTask must be reset")
        if not step.execution.succeeded:
            return TaskProgress(True, False, TerminationReason.BACKEND_ERROR)
        point = step.observation_after.position_ned
        distance = math.dist((point.north_m, point.east_m, point.down_m),
                             (self.target.north_m, self.target.east_m, self.target.down_m))
        if distance <= self.tolerance_m:
            return TaskProgress(True, True, TerminationReason.SUCCESS)
        return TaskProgress(True, False, TerminationReason.TASK_FAILED)

    def close(self) -> None:
        self.target = None
