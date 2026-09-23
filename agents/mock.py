from contracts import Action, ActionKind, PlatformObservation, PositionNed, TaskSpec


class FixedMoveAgent:
    def __init__(self, target: PositionNed, deadline_s: float = 5.0) -> None:
        self.target, self.deadline_s, self._vehicle_id = target, deadline_s, None
        self._sequence = 0

    def reset(self, task: TaskSpec, initial_observation: PlatformObservation) -> None:
        del task
        self._vehicle_id = initial_observation.vehicle_id
        self._sequence = 0

    def act(self, observation: PlatformObservation) -> Action:
        if self._vehicle_id is None:
            raise RuntimeError("agent must be reset")
        self._sequence += 1
        return Action(f"fixed-move-{self._sequence}", ActionKind.MOVE_TO, self._vehicle_id, self.deadline_s, self.target)

    def close(self) -> None:
        return None


class HoverAgent:
    """Second interchangeable baseline; never moves toward the target."""
    def __init__(self, deadline_s: float = 1.0) -> None:
        self.deadline_s = deadline_s
        self._vehicle_id: str | None = None
        self._sequence = 0

    def reset(self, task: TaskSpec, initial_observation: PlatformObservation) -> None:
        del task
        self._vehicle_id = initial_observation.vehicle_id
        self._sequence = 0

    def act(self, observation: PlatformObservation) -> Action:
        if self._vehicle_id is None:
            raise RuntimeError("agent must be reset")
        self._sequence += 1
        return Action(f"hover-{self._sequence}", ActionKind.HOVER, self._vehicle_id, self.deadline_s)

    def close(self) -> None:
        return None

class OffsetMoveAgent(FixedMoveAgent):
    """Mock comparison agent that deliberately misses the target to the north."""

    def __init__(self, offset_north_m: float = 1.0, deadline_s: float = 5.0) -> None:
        super().__init__(PositionNed(0, 0, 0), deadline_s)
        self.offset_north_m = offset_north_m

    def reset(self, task: TaskSpec, initial_observation: PlatformObservation) -> None:
        target = PositionNed.from_dict(task.parameters["target_position_ned"])
        self.target = PositionNed(target.north_m + self.offset_north_m, target.east_m, target.down_m)
        super().reset(task, initial_observation)
