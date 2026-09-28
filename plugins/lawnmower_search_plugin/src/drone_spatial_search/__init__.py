"""Independent Lawnmower Search Agent v0.2 candidate plugin for drone spatial search."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from contracts.data_v02 import (
    ActionChannel,
    ActionV02,
    ContractValidationError,
    EpisodeSnapshotV02,
    PlatformObservationV02,
)

ACTION_KIND = "spatial/move-to"
ACTION_SCHEMA = "spatial.move-to/v1"
REPORT_KIND = "spatial/report-target"
REPORT_SCHEMA = "spatial.report-target/v1"
COORDINATES = ("north_m", "east_m", "down_m")


def _finite_float(val: object, name: str) -> float:
    if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
        raise ContractValidationError(f"{name} must be a finite number")
    return float(val)


def _generate_axis_coordinates(
    min_val: float, max_val: float, spacing: float, axis_name: str
) -> list[float]:
    if min_val >= max_val:
        raise ContractValidationError(
            f"min_{axis_name} ({min_val}) must be strictly less than max_{axis_name} ({max_val})"
        )
    if spacing <= 0 or not math.isfinite(spacing):
        raise ContractValidationError(f"{axis_name} spacing must be finite and positive")

    coords = [min_val]
    step = 1
    max_steps = 10_000
    coord_scale = max(abs(min_val), abs(max_val))
    while True:
        candidate = min_val + step * spacing
        if candidate >= max_val:
            break
        if candidate <= coords[-1]:
            raise ContractValidationError(
                f"spacing ({spacing}) is below floating point resolution at scale {coord_scale}"
            )
        coords.append(candidate)
        step += 1
        if len(coords) > max_steps:
            raise ContractValidationError(
                f"Search lane count exceeded maximum allowable limit of {max_steps}"
            )

    if max_val <= coords[-1]:
        raise ContractValidationError(
            f"max_{axis_name} ({max_val}) is not strictly greater than previous coordinate ({coords[-1]})"
        )
    coords.append(max_val)

    for idx in range(len(coords) - 1):
        if coords[idx + 1] <= coords[idx]:
            raise ContractValidationError(
                f"Generated {axis_name} coordinates are not strictly increasing at index {idx}"
            )
    return coords


def _segment_clear_with_obstacles(
    start: tuple[float, float, float],
    goal: tuple[float, float, float],
    obstacles: Sequence[Mapping[str, float]],
) -> bool:
    delta_n = goal[0] - start[0]
    delta_e = goal[1] - start[1]
    length_sq = delta_n * delta_n + delta_e * delta_e
    for obs in obstacles:
        center_n = float(obs["center_north_m"])
        center_e = float(obs["center_east_m"])
        radius = float(obs["radius_m"])
        projection = (
            ((center_n - start[0]) * delta_n + (center_e - start[1]) * delta_e) / length_sq
            if length_sq
            else 0.0
        )
        fraction = max(0.0, min(1.0, projection))
        nearest_n = start[0] + fraction * delta_n
        nearest_e = start[1] + fraction * delta_e
        if math.hypot(nearest_n - center_n, nearest_e - center_e) <= radius:
            return False
    return True


def _generate_lawnmower_route(
    min_north: float,
    max_north: float,
    min_east: float,
    max_east: float,
    down: float,
    curr_north: float,
    curr_east: float,
    lane_spacing_m: float,
) -> tuple[tuple[float, float, float], ...]:
    east_coords = _generate_axis_coordinates(min_east, max_east, lane_spacing_m, "east")

    dist_to_min_e = abs(curr_east - min_east)
    dist_to_max_e = abs(curr_east - max_east)
    east_lanes = east_coords if dist_to_min_e <= dist_to_max_e else list(reversed(east_coords))

    dist_to_min_n = abs(curr_north - min_north)
    dist_to_max_n = abs(curr_north - max_north)
    start_at_min_n = dist_to_min_n <= dist_to_max_n

    waypoints: list[tuple[float, float, float]] = []
    for i, e in enumerate(east_lanes):
        lane_starts_at_min = (i % 2 == 0) if start_at_min_n else (i % 2 != 0)
        if lane_starts_at_min:
            p1 = (min_north, e, down)
            p2 = (max_north, e, down)
        else:
            p1 = (max_north, e, down)
            p2 = (min_north, e, down)
        waypoints.append(p1)
        waypoints.append(p2)
    return tuple(waypoints)


class LawnmowerSearchAgentV02:
    """Single-vehicle Lawnmower Search Agent v0.2 candidate plugin for spatial search tasks."""

    def __init__(
        self,
        lane_spacing_m: float = 2.0,
        min_confidence: float = 0.5,
        max_report_attempts: int = 3,
        waypoint_tolerance_m: float = 0.3,
        action_deadline_s: float = 1.0,
        flight_down_m: float | None = None,
        obstacles: Sequence[Mapping[str, float]] | None = None,
    ) -> None:
        self._lane_spacing_m = _finite_float(lane_spacing_m, "lane_spacing_m")
        if self._lane_spacing_m <= 0:
            raise ContractValidationError("lane_spacing_m must be strictly positive")

        self._min_confidence = _finite_float(min_confidence, "min_confidence")
        if not (0.0 <= self._min_confidence <= 1.0):
            raise ContractValidationError("min_confidence must be between 0.0 and 1.0")

        if (
            isinstance(max_report_attempts, bool)
            or not isinstance(max_report_attempts, int)
            or max_report_attempts < 1
        ):
            raise ContractValidationError("max_report_attempts must be an integer >= 1")
        self._max_report_attempts = max_report_attempts

        self._waypoint_tolerance_m = _finite_float(waypoint_tolerance_m, "waypoint_tolerance_m")
        if self._waypoint_tolerance_m <= 0:
            raise ContractValidationError("waypoint_tolerance_m must be strictly positive")

        self._action_deadline_s = _finite_float(action_deadline_s, "action_deadline_s")
        if self._action_deadline_s <= 0:
            raise ContractValidationError("action_deadline_s must be strictly positive")

        self._flight_down_m = (
            _finite_float(flight_down_m, "flight_down_m") if flight_down_m is not None else None
        )

        validated_obstacles: list[Mapping[str, float]] = []
        for obs in obstacles or ():
            if not isinstance(obs, Mapping):
                raise ContractValidationError("Each obstacle must be an object/mapping")
            for req in ("center_north_m", "center_east_m", "radius_m"):
                if req not in obs:
                    raise ContractValidationError(f"Obstacle missing required field '{req}'")
            cn = _finite_float(obs["center_north_m"], "obstacle center_north_m")
            ce = _finite_float(obs["center_east_m"], "obstacle center_east_m")
            rad = _finite_float(obs["radius_m"], "obstacle radius_m")
            if rad <= 0:
                raise ContractValidationError("Obstacle radius_m must be strictly positive")
            validated_obstacles.append({"center_north_m": cn, "center_east_m": ce, "radius_m": rad})
        self._obstacles = tuple(validated_obstacles)

        self._report_attempts: dict[str, int] = {}
        self._waypoints: tuple[tuple[float, float, float], ...] = ()
        self._waypoint_index: int = 0
        self._closed: bool = False

    @property
    def route(self) -> tuple[tuple[float, float, float], ...]:
        return self._waypoints

    @property
    def waypoint_index(self) -> int:
        return self._waypoint_index

    @property
    def reported_targets(self) -> frozenset[str]:
        return frozenset(k for k, v in self._report_attempts.items() if v > 0)

    @property
    def is_closed(self) -> bool:
        return self._closed

    def reset(self) -> None:
        """Reset internal trajectory, waypoint index, and report tracking between episodes."""
        self._report_attempts.clear()
        self._waypoints = ()
        self._waypoint_index = 0
        self._closed = False

    def close(self) -> None:
        """Close agent and clear all internal state."""
        self._report_attempts.clear()
        self._waypoints = ()
        self._waypoint_index = 0
        self._closed = True

    def act(
        self, observation_or_snapshot: PlatformObservationV02 | EpisodeSnapshotV02
    ) -> ActionV02:
        """Compute next search or reporting action. Strictly single-vehicle."""
        if self._closed:
            raise RuntimeError("LawnmowerSearchAgentV02 is closed; cannot act")

        if isinstance(observation_or_snapshot, PlatformObservationV02):
            return self._compute_action(observation_or_snapshot)

        if isinstance(observation_or_snapshot, EpisodeSnapshotV02):
            if len(observation_or_snapshot.observations) != 1:
                raise ContractValidationError(
                    f"LawnmowerSearchAgentV02 is strictly a single-vehicle agent; "
                    f"received snapshot with {len(observation_or_snapshot.observations)} vehicles"
                )
            obs = next(iter(observation_or_snapshot.observations.values()))
            return self._compute_action(obs)

        raise ContractValidationError(
            f"Expected PlatformObservationV02 or EpisodeSnapshotV02, got {type(observation_or_snapshot).__name__}"
        )

    def _validate_search_area(self, area: object) -> tuple[float, float, float, float, float, float]:
        if not isinstance(area, Mapping):
            raise ContractValidationError("observation state missing valid 'search_area' mapping")
        for axis in ("north", "east", "down"):
            if axis not in area:
                raise ContractValidationError(f"search_area missing required axis '{axis}'")
            bounds = area[axis]
            if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
                raise ContractValidationError(f"search_area['{axis}'] must be a 2-element array/tuple")
            b_min, b_max = bounds[0], bounds[1]
            if isinstance(b_min, bool) or not isinstance(b_min, (int, float)) or not math.isfinite(b_min):
                raise ContractValidationError(f"search_area['{axis}'][0] must be a finite number")
            if isinstance(b_max, bool) or not isinstance(b_max, (int, float)) or not math.isfinite(b_max):
                raise ContractValidationError(f"search_area['{axis}'][1] must be a finite number")
            if b_min >= b_max:
                raise ContractValidationError(
                    f"search_area['{axis}'] lower bound ({b_min}) must be strictly less than upper bound ({b_max})"
                )
        return (
            float(area["north"][0]), float(area["north"][1]),
            float(area["east"][0]), float(area["east"][1]),
            float(area["down"][0]), float(area["down"][1]),
        )

    def _compute_action(self, observation: PlatformObservationV02) -> ActionV02:
        state = observation.state
        if not isinstance(state, Mapping):
            raise ContractValidationError("observation state must be an object/mapping")

        # Truth isolation: Agent must NEVER access truth
        if "truth" in state or "ground_truth" in state:
            raise ContractValidationError("truth leaked into Agent observation state")

        # Validate mandatory position coordinates
        for key in COORDINATES:
            if key not in state:
                raise ContractValidationError(f"observation state missing required '{key}'")
            val = state[key]
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
                raise ContractValidationError(f"observation state coordinate '{key}' must be a finite number")

        curr_pos = (float(state["north_m"]), float(state["east_m"]), float(state["down_m"]))

        # Inspect platform-visible accepted reports (ground-truth echo)
        reports = state.get("reports", [])
        if not isinstance(reports, list):
            raise ContractValidationError("observation reports must be an array")
        accepted_target_ids: set[str] = set()
        for rep in reports:
            if isinstance(rep, Mapping) and "target_id" in rep and rep["target_id"]:
                accepted_target_ids.add(str(rep["target_id"]))

        # Check for credible target detections
        raw_detections = state.get("detections", [])
        if not isinstance(raw_detections, list):
            raise ContractValidationError("observation detections must be an array")

        credible_detections: list[Mapping[str, Any]] = []
        for det in raw_detections:
            if not isinstance(det, Mapping):
                continue
            tid = det.get("target_id")
            conf = det.get("confidence", 0.0)
            if (
                isinstance(tid, str)
                and tid
                # Deduplication: do NOT report if already accepted in platform reports
                and tid not in accepted_target_ids
                # Bounded retry: do NOT exceed max_report_attempts for unconfirmed targets
                and self._report_attempts.get(tid, 0) < self._max_report_attempts
                and isinstance(conf, (int, float))
                and not isinstance(conf, bool)
                and math.isfinite(conf)
                and conf >= self._min_confidence
                and all(
                    k in det
                    and isinstance(det[k], (int, float))
                    and not isinstance(det[k], bool)
                    and math.isfinite(det[k])
                    for k in COORDINATES
                )
            ):
                credible_detections.append(det)

        if credible_detections:
            best = max(credible_detections, key=lambda d: float(d["confidence"]))
            target_id = str(best["target_id"])
            self._report_attempts[target_id] = self._report_attempts.get(target_id, 0) + 1
            payload = {
                "target_id": target_id,
                "north_m": float(best["north_m"]),
                "east_m": float(best["east_m"]),
                "down_m": float(best["down_m"]),
            }
            return ActionV02(
                f"search-report-{observation.sequence}-{observation.vehicle_id}",
                observation.vehicle_id,
                REPORT_KIND,
                ActionChannel.REPORT,
                REPORT_SCHEMA,
                payload,
                self._action_deadline_s,
            )

        # No credible detection to report: execute Lawnmower search route
        if not self._waypoints:
            min_n, max_n, min_e, max_e, min_d, max_d = self._validate_search_area(state.get("search_area"))
            flight_down = (
                self._flight_down_m
                if self._flight_down_m is not None
                else curr_pos[2]
            )
            planned = _generate_lawnmower_route(
                min_north=min_n,
                max_north=max_n,
                min_east=min_e,
                max_east=max_e,
                down=flight_down,
                curr_north=curr_pos[0],
                curr_east=curr_pos[1],
                lane_spacing_m=self._lane_spacing_m,
            )

            # Actively verify entire planned route against configured obstacles
            if self._obstacles:
                if planned and not _segment_clear_with_obstacles(curr_pos, planned[0], self._obstacles):
                    raise ContractValidationError(
                        f"Path from current position {curr_pos} to first waypoint {planned[0]} "
                        f"crosses configured obstacle"
                    )
                for idx in range(len(planned) - 1):
                    if not _segment_clear_with_obstacles(planned[idx], planned[idx + 1], self._obstacles):
                        raise ContractValidationError(
                            f"Lawnmower route segment from {planned[idx]} to {planned[idx + 1]} "
                            f"crosses configured obstacle"
                        )

            self._waypoints = planned
            self._waypoint_index = 0

        # Advance reached waypoints
        while (
            self._waypoint_index < len(self._waypoints)
            and math.dist(curr_pos, self._waypoints[self._waypoint_index]) <= self._waypoint_tolerance_m
        ):
            self._waypoint_index += 1

        if self._waypoint_index < len(self._waypoints):
            target_pt = self._waypoints[self._waypoint_index]
        else:
            # Route fully traversed without detection: hold position
            target_pt = curr_pos

        # Check commanded movement against configured obstacles before emitting action
        if self._obstacles and not _segment_clear_with_obstacles(curr_pos, target_pt, self._obstacles):
            raise ContractValidationError(
                f"Commanded movement from {curr_pos} to {target_pt} crosses configured obstacle"
            )

        payload = {
            "north_m": target_pt[0],
            "east_m": target_pt[1],
            "down_m": target_pt[2],
        }
        return ActionV02(
            f"search-move-{observation.sequence}-{observation.vehicle_id}",
            observation.vehicle_id,
            ACTION_KIND,
            ActionChannel.CONTROL,
            ACTION_SCHEMA,
            payload,
            self._action_deadline_s,
        )


def create_agent(
    *, config: Mapping[str, Any] | None = None, context: Any = None
) -> LawnmowerSearchAgentV02:
    """Component factory for LawnmowerSearchAgentV02."""
    cfg = config or {}
    lane_spacing_m = float(cfg.get("lane_spacing_m", 2.0))
    min_confidence = float(cfg.get("min_confidence", 0.5))
    max_report_attempts = int(cfg.get("max_report_attempts", 3))
    waypoint_tolerance_m = float(cfg.get("waypoint_tolerance_m", 0.3))
    action_deadline_s = float(cfg.get("action_deadline_s", 1.0))
    flight_down_m = (
        float(cfg["flight_down_m"])
        if "flight_down_m" in cfg and cfg["flight_down_m"] is not None
        else None
    )
    obstacles = cfg.get("obstacles", ())
    return LawnmowerSearchAgentV02(
        lane_spacing_m=lane_spacing_m,
        min_confidence=min_confidence,
        max_report_attempts=max_report_attempts,
        waypoint_tolerance_m=waypoint_tolerance_m,
        action_deadline_s=action_deadline_s,
        flight_down_m=flight_down_m,
        obstacles=obstacles,
    )
