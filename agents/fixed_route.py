"""Formal FixedRouteAgent for multi-waypoint reach_point tasks under platform contract v0.1."""
from __future__ import annotations

import math
from typing import Mapping, Sequence

from contracts import (
    Action,
    ActionKind,
    ContractValidationError,
    PlatformObservation,
    PositionNed,
    TaskSpec,
)


class FixedRouteAgent:
    """Agent that commands a drone through a sequence of waypoints to a final NED target."""

    def __init__(self) -> None:
        self._route: tuple[PositionNed, ...] = ()
        self._waypoint_tolerance_m: float = 0.5
        self._target_tolerance_m: float = 0.5
        self._action_deadline_s: float = 5.0
        self._vehicle_id: str | None = None
        self._current_index: int = 0
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

        # Parse intermediate waypoints
        waypoints: list[PositionNed] = []
        if "intermediate_waypoints_ned" in params:
            raw_waypoints = params["intermediate_waypoints_ned"]
            if not isinstance(raw_waypoints, list):
                raise ContractValidationError(
                    "intermediate_waypoints_ned must be an array"
                )
            for item in raw_waypoints:
                waypoints.append(PositionNed.from_dict(item))

        # Build route: append target_position_ned unless last waypoint is already identical
        if waypoints and waypoints[-1] == target_position_ned:
            route = tuple(waypoints)
        else:
            route = tuple(waypoints + [target_position_ned])

        # Parse tolerance_m (for final target)
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
            target_tolerance_m = float(tol)
        else:
            target_tolerance_m = 0.5

        # Parse waypoint_tolerance_m (for intermediate waypoints, falls back to tolerance_m)
        if "waypoint_tolerance_m" in params:
            wtol = params["waypoint_tolerance_m"]
            if (
                isinstance(wtol, bool)
                or not isinstance(wtol, (int, float))
                or not math.isfinite(wtol)
                or wtol <= 0
            ):
                raise ContractValidationError(
                    "waypoint_tolerance_m must be a positive finite number"
                )
            waypoint_tolerance_m = float(wtol)
        else:
            waypoint_tolerance_m = target_tolerance_m

        # Parse action_deadline_s
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
        self._route = route
        self._waypoint_tolerance_m = waypoint_tolerance_m
        self._target_tolerance_m = target_tolerance_m
        self._action_deadline_s = action_deadline_s
        self._vehicle_id = vehicle_id
        self._current_index = 0
        self._action_count = 0

    def act(self, observation: PlatformObservation) -> Action:
        """Produce next action based on current observation and waypoint progression."""
        if self._vehicle_id is None or not self._route:
            raise RuntimeError("agent must be reset before calling act()")

        if observation.vehicle_id != self._vehicle_id:
            raise ContractValidationError(
                f"observation vehicle_id '{observation.vehicle_id}' "
                f"does not match agent vehicle_id '{self._vehicle_id}'"
            )

        pos = observation.position_ned
        num_waypoints = len(self._route)

        # Advance through intermediate waypoints if within waypoint tolerance
        while self._current_index < num_waypoints - 1:
            tgt = self._route[self._current_index]
            dist = math.dist(
                (pos.north_m, pos.east_m, pos.down_m),
                (tgt.north_m, tgt.east_m, tgt.down_m),
            )
            if dist <= self._waypoint_tolerance_m:
                self._current_index += 1
            else:
                break

        final_tgt = self._route[-1]
        final_dist = math.dist(
            (pos.north_m, pos.east_m, pos.down_m),
            (final_tgt.north_m, final_tgt.east_m, final_tgt.down_m),
        )

        self._action_count += 1
        action_id = f"fixed-route-{self._action_count}"

        # If at final target and reached within final tolerance, HOVER
        if self._current_index == num_waypoints - 1 and final_dist <= self._target_tolerance_m:
            return Action(
                action_id=action_id,
                kind=ActionKind.HOVER,
                vehicle_id=self._vehicle_id,
                deadline_s=self._action_deadline_s,
                target_position_ned=None,
            )
        else:
            current_target = self._route[self._current_index]
            return Action(
                action_id=action_id,
                kind=ActionKind.MOVE_TO,
                vehicle_id=self._vehicle_id,
                deadline_s=self._action_deadline_s,
                target_position_ned=current_target,
            )

    def close(self) -> None:
        """Release episode state."""
        self._route = ()
        self._vehicle_id = None
        self._current_index = 0
        self._action_count = 0
        self._waypoint_tolerance_m = 0.5
        self._target_tolerance_m = 0.5
        self._action_deadline_s = 5.0
