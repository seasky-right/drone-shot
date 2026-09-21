from contracts import Action, ActionKind, PlatformObservation, PositionNed, TaskSpec


class FixedMoveAgent:
    def __init__(self, target: PositionNed, deadline_s: float = 5.0) -> None:
        self.target, self.deadline_s, self._vehicle_id = target, deadline_s, None

    def reset(self, task: TaskSpec, initial_observation: PlatformObservation) -> None:
        del task
        self._vehicle_id = initial_observation.vehicle_id

    def act(self, observation: PlatformObservation) -> Action:
        if self._vehicle_id is None:
            raise RuntimeError("agent must be reset")
        return Action("fixed-move-1", ActionKind.MOVE_TO, self._vehicle_id, self.deadline_s, self.target)

    def close(self) -> None:
        return None
