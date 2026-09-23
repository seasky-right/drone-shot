"""Simulator-independent baseline agents for ReachPoint benchmarks."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from typing import Mapping

from contracts import (
    Action, ActionKind, ContractValidationError, PlatformObservation, PositionNed,
    TaskSpec,
)
from tasks.reach_point import ReachPointRules, distance_m


def _positive(value: object, name: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ContractValidationError(f"{name} must be finite and positive")
    return float(value)


class _BaseAgent:
    def __init__(self, deadline_s: float) -> None:
        self.deadline_s = _positive(deadline_s, "deadline_s")
        self._vehicle_id: str | None = None
        self._action_number = 0

    def reset(self, task: TaskSpec, initial_observation: PlatformObservation) -> None:
        del task
        self._vehicle_id = initial_observation.vehicle_id
        self._action_number = 0

    def _action(self, kind: ActionKind, target: PositionNed | None = None) -> Action:
        if self._vehicle_id is None:
            raise RuntimeError("agent must be reset before act")
        action = Action(f"{type(self).__name__}-{self._action_number}", kind,
                        self._vehicle_id, self.deadline_s, target)
        self._action_number += 1
        return action

    def close(self) -> None:
        self._vehicle_id = None


class RandomAgent(_BaseAgent):
    """Weak deterministic baseline: samples a local NED move from TaskSpec.seed."""

    def __init__(self, radius_m: float = 2.0, deadline_s: float = 5.0) -> None:
        super().__init__(deadline_s)
        self.radius_m = _positive(radius_m, "radius_m")
        self._random: random.Random | None = None

    def reset(self, task: TaskSpec, initial_observation: PlatformObservation) -> None:
        super().reset(task, initial_observation)
        self._random = random.Random(task.seed)

    def act(self, observation: PlatformObservation) -> Action:
        if self._random is None:
            raise RuntimeError("agent must be reset before act")
        p = observation.position_ned
        target = PositionNed(
            p.north_m + self._random.uniform(-self.radius_m, self.radius_m),
            p.east_m + self._random.uniform(-self.radius_m, self.radius_m),
            p.down_m + self._random.uniform(-self.radius_m, self.radius_m),
        )
        return self._action(ActionKind.MOVE_TO, target)

    def close(self) -> None:
        super().close()
        self._random = None


class FixedRouteAgent(_BaseAgent):
    """Follows configured world-NED waypoints, then hovers."""

    def __init__(self, route: Sequence[PositionNed | Mapping[str, object]],
                 deadline_s: float = 5.0) -> None:
        super().__init__(deadline_s)
        if not route:
            raise ContractValidationError("route must contain waypoints")
        self.route = tuple(point if isinstance(point, PositionNed)
                           else PositionNed.from_dict(point) for point in route)
        self._next = 0

    def reset(self, task: TaskSpec, initial_observation: PlatformObservation) -> None:
        super().reset(task, initial_observation)
        self._next = 0

    def act(self, observation: PlatformObservation) -> Action:
        del observation
        if self._next >= len(self.route):
            return self._action(ActionKind.HOVER)
        target = self.route[self._next]
        self._next += 1
        return self._action(ActionKind.MOVE_TO, target)


class RuleBasedAgent(_BaseAgent):
    """Direct-to-goal baseline that hovers after an observed arrival."""

    def __init__(self, deadline_s: float = 5.0) -> None:
        super().__init__(deadline_s)
        self._rules: ReachPointRules | None = None

    def reset(self, task: TaskSpec, initial_observation: PlatformObservation) -> None:
        super().reset(task, initial_observation)
        self._rules = ReachPointRules.from_spec(task)

    def act(self, observation: PlatformObservation) -> Action:
        if self._rules is None:
            raise RuntimeError("agent must be reset before act")
        if distance_m(observation.position_ned, self._rules.target_position_ned) <= self._rules.tolerance_m:
            return self._action(ActionKind.HOVER)
        return self._action(ActionKind.MOVE_TO, self._rules.target_position_ned)

    def close(self) -> None:
        super().close()
        self._rules = None
