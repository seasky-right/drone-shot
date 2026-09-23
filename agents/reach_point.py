"""ReachPoint agents for runtime use and benchmark baselines."""
from __future__ import annotations

import math
import random
from collections.abc import Sequence
from typing import Mapping

from contracts import (
    Action,
    ActionKind,
    ContractValidationError,
    PlatformObservation,
    PositionNed,
    TaskSpec,
)
from tasks.reach_point import ReachPointRules, distance_m


class DirectPointAgent:
    """Agent that commands a drone directly to a target NED position, then hovers."""

    def __init__(self) -> None:
        self._target_position_ned: PositionNed | None = None
        self._tolerance_m: float = 0.5
        self._action_deadline_s: float = 5.0
        self._vehicle_id: str | None = None
        self._action_count: int = 0

    def reset(self, task: TaskSpec, initial_observation: PlatformObservation) -> None:
        """Initialize the agent from TaskSpec and initial observation.

        Guarantees atomicity: if reset fails for any reason, existing state is cleared
        and the agent remains in an uninitialized state.
        """
        # Clear existing state immediately to prevent hybrid state on validation failure
        self.close()

        if task.task_type != "reach_point":
            raise ContractValidationError(
                f"task_type must be reach_point, got '{task.task_type}'"
            )

        params = task.parameters
        if not isinstance(params, Mapping):
            raise ContractValidationError("task parameters must be an object")

        if "target_position_ned" not in params:
            raise ContractValidationError(
                "task parameters missing required 'target_position_ned'"
            )

        target_position_ned = PositionNed.from_dict(params["target_position_ned"])

        if "tolerance_m" in params:
            tol = params["tolerance_m"]
            if (
                isinstance(tol, bool)
                or not isinstance(tol, (int, float))
                or not math.isfinite(tol)
                or tol <= 0
            ):
                raise ContractValidationError(
                    "tolerance_m must be a positive finite number"
                )
            tolerance_m = float(tol)
        else:
            tolerance_m = 0.5

        if "action_deadline_s" in params:
            dl = params["action_deadline_s"]
            if (
                isinstance(dl, bool)
                or not isinstance(dl, (int, float))
                or not math.isfinite(dl)
                or dl <= 0
            ):
                raise ContractValidationError(
                    "action_deadline_s must be a positive finite number"
                )
            action_deadline_s = float(dl)
        else:
            action_deadline_s = 5.0

        vehicle_id = initial_observation.vehicle_id

        # Commit validated state atomically
        self._target_position_ned = target_position_ned
        self._tolerance_m = tolerance_m
        self._action_deadline_s = action_deadline_s
        self._vehicle_id = vehicle_id
        self._action_count = 0

    def act(self, observation: PlatformObservation) -> Action:
        """Produce next action based on current observation."""
        if self._vehicle_id is None or self._target_position_ned is None:
            raise RuntimeError("agent must be reset before calling act()")

        if observation.vehicle_id != self._vehicle_id:
            raise ContractValidationError(
                f"observation vehicle_id '{observation.vehicle_id}' "
                f"does not match agent vehicle_id '{self._vehicle_id}'"
            )

        self._action_count += 1
        action_id = f"direct-point-{self._action_count}"

        pos = observation.position_ned
        tgt = self._target_position_ned
        distance = math.dist(
            (pos.north_m, pos.east_m, pos.down_m),
            (tgt.north_m, tgt.east_m, tgt.down_m),
        )

        if distance > self._tolerance_m:
            return Action(
                action_id=action_id,
                kind=ActionKind.MOVE_TO,
                vehicle_id=self._vehicle_id,
                deadline_s=self._action_deadline_s,
                target_position_ned=tgt,
            )
        else:
            return Action(
                action_id=action_id,
                kind=ActionKind.HOVER,
                vehicle_id=self._vehicle_id,
                deadline_s=self._action_deadline_s,
                target_position_ned=None,
            )

    def close(self) -> None:
        """Release episode state."""
        self._target_position_ned = None
        self._vehicle_id = None
        self._action_count = 0


def _positive(value: object, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ContractValidationError(f"{name} must be finite and positive")
    return float(value)


class _BaseAgent:
    """Small base for deterministic, simulator-independent benchmark agents."""

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
        action = Action(
            f"{type(self).__name__}-{self._action_number}",
            kind,
            self._vehicle_id,
            self.deadline_s,
            target,
        )
        self._action_number += 1
        return action

    def close(self) -> None:
        self._vehicle_id = None


class RandomAgent(_BaseAgent):
    """Weak deterministic baseline that samples local NED moves from TaskSpec.seed."""

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
        position = observation.position_ned
        target = PositionNed(
            position.north_m + self._random.uniform(-self.radius_m, self.radius_m),
            position.east_m + self._random.uniform(-self.radius_m, self.radius_m),
            position.down_m + self._random.uniform(-self.radius_m, self.radius_m),
        )
        return self._action(ActionKind.MOVE_TO, target)

    def close(self) -> None:
        super().close()
        self._random = None


class FixedRouteAgent(_BaseAgent):
    """Follow configured world NED waypoints, then hover."""

    def __init__(
        self,
        route: Sequence[PositionNed | Mapping[str, object]],
        deadline_s: float = 5.0,
    ) -> None:
        super().__init__(deadline_s)
        if not route:
            raise ContractValidationError("route must contain waypoints")
        self.route = tuple(
            point if isinstance(point, PositionNed) else PositionNed.from_dict(point)
            for point in route
        )
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
    """Move directly to the goal and hover after an observed arrival."""

    def __init__(self, deadline_s: float = 5.0) -> None:
        super().__init__(deadline_s)
        self._rules: ReachPointRules | None = None

    def reset(self, task: TaskSpec, initial_observation: PlatformObservation) -> None:
        super().reset(task, initial_observation)
        self._rules = ReachPointRules.from_spec(task)

    def act(self, observation: PlatformObservation) -> Action:
        if self._rules is None:
            raise RuntimeError("agent must be reset before act")
        if (
            distance_m(
                observation.position_ned,
                self._rules.target_position_ned,
            )
            <= self._rules.tolerance_m
        ):
            return self._action(ActionKind.HOVER)
        return self._action(ActionKind.MOVE_TO, self._rules.target_position_ned)

    def close(self) -> None:
        super().close()
        self._rules = None
