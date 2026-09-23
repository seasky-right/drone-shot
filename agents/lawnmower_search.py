"""Formal LawnmowerSearchAgent for rectangular area search navigation under platform contract v0.1."""
from __future__ import annotations

import math
from typing import Mapping

from contracts import (
    Action,
    ActionKind,
    ContractValidationError,
    PlatformObservation,
    PositionNed,
    TaskSpec,
)


def _finite_float(val: object, name: str) -> float:
    if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
        raise ContractValidationError(f"{name} must be a finite number")
    return float(val)


class LawnmowerSearchAgent:
    """Agent that generates and executes a reciprocating/serpentine lawnmower search route."""

    def __init__(self) -> None:
        self._route: tuple[PositionNed, ...] = ()
        self._waypoint_tolerance_m: float = 0.5
        self._action_deadline_s: float = 5.0
        self._vehicle_id: str | None = None
        self._current_index: int = 0
        self._action_count: int = 0
        self._completed: bool = False

    @property
    def route(self) -> tuple[PositionNed, ...]:
        """Return the immutable sequence of planned route waypoints."""
        return self._route

    def reset(self, task: TaskSpec, initial_observation: PlatformObservation) -> None:
        """Initialize the agent from TaskSpec and initial observation.

        Guarantees atomicity: if reset fails for any reason, existing state is cleared
        and the agent remains in an uninitialized state.
        """
        # Clear existing state immediately to prevent hybrid state on validation failure
        self.close()

        if task.task_type != "search_target":
            raise ContractValidationError(
                f"task_type must be search_target, got '{task.task_type}'"
            )

        params = task.parameters
        if not isinstance(params, Mapping):
            raise ContractValidationError("task parameters must be an object")

        if "search_area_ned" not in params:
            raise ContractValidationError(
                "task parameters missing required 'search_area_ned'"
            )

        area = params["search_area_ned"]
        if not isinstance(area, Mapping):
            raise ContractValidationError("search_area_ned must be an object")

        required_area_keys = (
            "min_north_m",
            "max_north_m",
            "min_east_m",
            "max_east_m",
            "flight_down_m",
        )
        for key in required_area_keys:
            if key not in area:
                raise ContractValidationError(
                    f"search_area_ned missing required '{key}'"
                )

        min_north_m = _finite_float(area["min_north_m"], "min_north_m")
        max_north_m = _finite_float(area["max_north_m"], "max_north_m")
        if min_north_m >= max_north_m:
            raise ContractValidationError(
                f"min_north_m ({min_north_m}) must be strictly less than max_north_m ({max_north_m})"
            )

        min_east_m = _finite_float(area["min_east_m"], "min_east_m")
        max_east_m = _finite_float(area["max_east_m"], "max_east_m")
        if min_east_m >= max_east_m:
            raise ContractValidationError(
                f"min_east_m ({min_east_m}) must be strictly less than max_east_m ({max_east_m})"
            )

        flight_down_m = _finite_float(area["flight_down_m"], "flight_down_m")

        if "lane_spacing_m" not in params:
            raise ContractValidationError(
                "task parameters missing required 'lane_spacing_m'"
            )
        lane_spacing_m = _finite_float(params["lane_spacing_m"], "lane_spacing_m")
        if lane_spacing_m <= 0:
            raise ContractValidationError("lane_spacing_m must be strictly positive")

        if "waypoint_tolerance_m" in params:
            waypoint_tolerance_m = _finite_float(
                params["waypoint_tolerance_m"], "waypoint_tolerance_m"
            )
            if waypoint_tolerance_m <= 0:
                raise ContractValidationError(
                    "waypoint_tolerance_m must be strictly positive"
                )
        else:
            waypoint_tolerance_m = 0.5

        if "action_deadline_s" in params:
            action_deadline_s = _finite_float(
                params["action_deadline_s"], "action_deadline_s"
            )
            if action_deadline_s <= 0:
                raise ContractValidationError(
                    "action_deadline_s must be strictly positive"
                )
        else:
            action_deadline_s = 5.0

        vehicle_id = initial_observation.vehicle_id
        if not isinstance(vehicle_id, str) or not vehicle_id:
            raise ContractValidationError(
                "initial_observation vehicle_id must be non-empty text"
            )

        init_pos = initial_observation.position_ned
        if not isinstance(init_pos, PositionNed):
            raise ContractValidationError(
                "initial_observation position_ned must be PositionNed"
            )

        MAX_LANES = 10_000

        east_width = max_east_m - min_east_m
        if not math.isfinite(east_width) or east_width <= 0:
            raise ContractValidationError(
                f"min_east_m ({min_east_m}) must be strictly less than max_east_m ({max_east_m})"
            )

        estimated_lanes = (east_width / lane_spacing_m) + 2.0
        if not math.isfinite(estimated_lanes) or estimated_lanes > MAX_LANES:
            raise ContractValidationError(
                f"lane_spacing_m ({lane_spacing_m}) produces too many search lanes "
                f"(estimated {estimated_lanes:.0f} > maximum {MAX_LANES})"
            )

        coord_scale = max(abs(min_east_m), abs(max_east_m))

        # Generate east coordinates: strictly starting with min_east_m and ending with max_east_m
        east_coords: list[float] = [min_east_m]
        step = 1
        while True:
            candidate = min_east_m + step * lane_spacing_m
            if candidate >= max_east_m:
                break
            if candidate <= east_coords[-1]:
                raise ContractValidationError(
                    f"lane_spacing_m ({lane_spacing_m}) is below floating point resolution at coordinate scale {coord_scale}"
                )
            east_coords.append(candidate)
            step += 1
            if len(east_coords) > MAX_LANES:
                raise ContractValidationError(
                    f"Search lane count exceeded maximum allowable limit of {MAX_LANES}"
                )

        if max_east_m <= east_coords[-1]:
            raise ContractValidationError(
                f"max_east_m ({max_east_m}) is not strictly greater than previous coordinate ({east_coords[-1]})"
            )
        east_coords.append(max_east_m)

        # Verify strict monotonicity invariant
        for idx in range(len(east_coords) - 1):
            if east_coords[idx + 1] <= east_coords[idx]:
                raise ContractValidationError(
                    f"Generated east coordinates are not strictly increasing at index {idx}: "
                    f"{east_coords[idx]} >= {east_coords[idx + 1]}"
                )

        # Determine starting north endpoint based on distance from initial position
        dist_to_min = abs(init_pos.north_m - min_north_m)
        dist_to_max = abs(init_pos.north_m - max_north_m)
        # If equidistant, start at min_north_m
        start_at_min = dist_to_min <= dist_to_max

        # Generate alternating route waypoints along north direction
        waypoints: list[PositionNed] = []
        for i, e in enumerate(east_coords):
            if start_at_min:
                lane_starts_at_min = (i % 2 == 0)
            else:
                lane_starts_at_min = (i % 2 != 0)

            if lane_starts_at_min:
                p1 = PositionNed(min_north_m, e, flight_down_m)
                p2 = PositionNed(max_north_m, e, flight_down_m)
            else:
                p1 = PositionNed(max_north_m, e, flight_down_m)
                p2 = PositionNed(min_north_m, e, flight_down_m)

            waypoints.append(p1)
            waypoints.append(p2)

        # Commit validated state atomically
        self._route = tuple(waypoints)
        self._waypoint_tolerance_m = waypoint_tolerance_m
        self._action_deadline_s = action_deadline_s
        self._vehicle_id = vehicle_id
        self._current_index = 0
        self._action_count = 0
        self._completed = False

    def act(self, observation: PlatformObservation) -> Action:
        """Produce next action based on current observation and route progression."""
        if self._vehicle_id is None or not self._route:
            raise RuntimeError("agent must be reset before calling act()")

        if observation.vehicle_id != self._vehicle_id:
            raise ContractValidationError(
                f"observation vehicle_id '{observation.vehicle_id}' "
                f"does not match agent vehicle_id '{self._vehicle_id}'"
            )

        pos = observation.position_ned
        num_waypoints = len(self._route)

        # Advance through waypoints if within waypoint tolerance
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

        # Check if final waypoint is reached
        final_tgt = self._route[-1]
        final_dist = math.dist(
            (pos.north_m, pos.east_m, pos.down_m),
            (final_tgt.north_m, final_tgt.east_m, final_tgt.down_m),
        )

        if self._current_index == num_waypoints - 1 and final_dist <= self._waypoint_tolerance_m:
            self._completed = True

        self._action_count += 1
        action_id = f"lawnmower-search-{self._action_count}"

        if self._completed:
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
        self._action_deadline_s = 5.0
        self._completed = False
