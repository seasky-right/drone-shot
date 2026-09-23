"""Deterministic route-following Agent using only v0.1 move_to and hover."""
from __future__ import annotations

from collections.abc import Sequence

from contracts import Action, ActionKind, ContractValidationError, PlatformObservation, PositionNed, TaskSpec
from tasks.reachpoint import ReachPointRules, distance_m


class FixedRouteAgent:
    def __init__(self, waypoints: Sequence[PositionNed] | None = None, deadline_s: float = 5.0) -> None:
        if isinstance(deadline_s, bool) or not isinstance(deadline_s, (int, float)) or not (0 < deadline_s < float("inf")):
            raise ContractValidationError("deadline_s must be a finite positive number")
        self._configured_waypoints = tuple(waypoints) if waypoints is not None else None
        if self._configured_waypoints is not None and (not self._configured_waypoints or not all(isinstance(p, PositionNed) for p in self._configured_waypoints)):
            raise ContractValidationError("waypoints must be a non-empty sequence of PositionNed")
        self.deadline_s = float(deadline_s)
        self._waypoints: tuple[PositionNed, ...] = ()
        self._vehicle_id: str | None = None
        self._tolerance_m = 0.5
        self._next_index = 0
        self._action_number = 0

    def reset(self, task: TaskSpec, initial_observation: PlatformObservation) -> None:
        rules = ReachPointRules.from_spec(task)
        raw_route = task.parameters.get("waypoints_ned")
        if self._configured_waypoints is not None:
            route = self._configured_waypoints
        elif raw_route is not None:
            if not isinstance(raw_route, list) or not raw_route:
                raise ContractValidationError("waypoints_ned must be a non-empty array")
            route = tuple(PositionNed.from_dict(point) for point in raw_route)
        else:
            route = (rules.target,)
        if distance_m(route[-1], rules.target) > rules.tolerance_m:
            raise ContractValidationError("final route waypoint must reach target")
        self._waypoints = route
        self._vehicle_id = initial_observation.vehicle_id
        self._tolerance_m = rules.tolerance_m
        self._next_index = 0
        self._action_number = 0

    def act(self, observation: PlatformObservation) -> Action:
        if self._vehicle_id is None:
            raise RuntimeError("FixedRouteAgent must be reset")
        if observation.vehicle_id != self._vehicle_id:
            raise ContractValidationError("observation vehicle does not match agent vehicle")
        while self._next_index < len(self._waypoints) and distance_m(observation.position_ned, self._waypoints[self._next_index]) <= self._tolerance_m:
            self._next_index += 1
        self._action_number += 1
        action_id = f"route-{self._action_number}"
        if self._next_index < len(self._waypoints):
            return Action(action_id, ActionKind.MOVE_TO, self._vehicle_id, self.deadline_s, self._waypoints[self._next_index])
        return Action(action_id, ActionKind.HOVER, self._vehicle_id, self.deadline_s)

    def close(self) -> None:
        self._waypoints = ()
        self._vehicle_id = None
