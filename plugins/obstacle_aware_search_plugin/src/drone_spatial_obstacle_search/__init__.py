"""Obstacle-aware boustrophedon spatial search agent plugin for drone platform v0.2.

Features:
- Plan collision-free boustrophedon sweep routes with A* geometric detour around known obstacles.
- Enforce strict safety margin around circular obstacles.
- Explicitly fail with ContractValidationError before emitting dangerous actions when promised route is blocked.
- Truth isolation: strictly avoids reading hidden world truth or Task/Evaluator secrets.
- Echo-based report deduplication and bounded retry (when episode continues).
- Strictly single-vehicle runtime enforcement.
- Bounded computational scale: hard limits on lane counts, obstacle counts, graph nodes, and A* iterations.
"""

from __future__ import annotations

import heapq
import math
from typing import Any, Mapping, Sequence, Tuple

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

# Explanatory operational limits to strictly bound memory and CPU workload
MAX_LANE_COUNT = 500
MAX_OBSTACLES = 50
MAX_GRAPH_NODES = 400
MAX_ASTAR_ITERATIONS = 1000
MAX_PLANNED_WAYPOINTS = 2000


def _finite_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractValidationError(f"{name} must be a number, got {type(value).__name__}")
    f_val = float(value)
    if not math.isfinite(f_val):
        raise ContractValidationError(f"{name} must be finite")
    return f_val


def _generate_axis_coordinates(
    min_coord: float, max_coord: float, step: float, axis_name: str = "axis"
) -> tuple[float, ...]:
    """Generate strictly monotonically increasing coordinates from min to max with bounded scale."""
    if min_coord >= max_coord:
        raise ContractValidationError(
            f"min_{axis_name} ({min_coord}) must be strictly less than max_{axis_name} ({max_coord})"
        )
    if step <= 0 or not math.isfinite(step):
        raise ContractValidationError(f"{axis_name} step must be finite and strictly positive")

    span = max_coord - min_coord
    estimated_count = span / step
    if estimated_count > MAX_LANE_COUNT:
        raise ContractValidationError(
            f"Estimated {axis_name} lane count ({estimated_count:.0f}) exceeds "
            f"maximum allowable limit of {MAX_LANE_COUNT}"
        )

    coords = [min_coord]
    i = 1
    coord_scale = max(abs(min_coord), abs(max_coord), 1.0)
    while True:
        candidate = min_coord + i * step
        if candidate >= max_coord:
            break
        if candidate <= coords[-1]:
            raise ContractValidationError(
                f"{axis_name} spacing ({step}) is below floating point resolution at scale {coord_scale}"
            )
        coords.append(candidate)
        i += 1
        if len(coords) > MAX_LANE_COUNT:
            raise ContractValidationError(
                f"Generated {axis_name} lane count exceeded maximum allowable limit of {MAX_LANE_COUNT}"
            )

    if coords[-1] != max_coord:
        coords.append(max_coord)
    return tuple(coords)


def _point_clear_with_obstacles(
    point_n: float,
    point_e: float,
    obstacles: Sequence[Mapping[str, float]],
    safety_margin_m: float = 0.0,
) -> bool:
    """Check if 2D position is strictly outside all obstacles including safety margin."""
    for obs in obstacles:
        cn = float(obs["center_north_m"])
        ce = float(obs["center_east_m"])
        r_eff = float(obs["radius_m"]) + safety_margin_m
        if math.hypot(point_n - cn, point_e - ce) <= r_eff:
            return False
    return True


def _segment_clear_with_obstacles(
    start: tuple[float, float, float] | tuple[float, float],
    goal: tuple[float, float, float] | tuple[float, float],
    obstacles: Sequence[Mapping[str, float]],
    safety_margin_m: float = 0.0,
) -> bool:
    """Check if 2D line segment between start and goal clears all obstacles."""
    start_n, start_e = start[0], start[1]
    goal_n, goal_e = goal[0], goal[1]
    delta_n = goal_n - start_n
    delta_e = goal_e - start_e
    length_sq = delta_n * delta_n + delta_e * delta_e

    for obs in obstacles:
        cn = float(obs["center_north_m"])
        ce = float(obs["center_east_m"])
        r_eff = float(obs["radius_m"]) + safety_margin_m
        if length_sq == 0.0:
            if math.hypot(start_n - cn, start_e - ce) <= r_eff:
                return False
            continue

        projection = ((cn - start_n) * delta_n + (ce - start_e) * delta_e) / length_sq
        fraction = max(0.0, min(1.0, projection))
        nearest_n = start_n + fraction * delta_n
        nearest_e = start_e + fraction * delta_e
        if math.hypot(nearest_n - cn, nearest_e - ce) <= r_eff:
            return False
    return True


def _plan_detour(
    start: tuple[float, float],
    goal: tuple[float, float],
    obstacles: Sequence[Mapping[str, float]],
    bounds: Mapping[str, tuple[float, float]],
    safety_margin_m: float = 0.25,
) -> list[tuple[float, float]] | None:
    """Compute shortest collision-free path from start to goal around obstacles using A*.

    Returns list of waypoints ending with goal (excluding start), or None if no safe path exists.
    """
    if _segment_clear_with_obstacles(start, goal, obstacles, safety_margin_m):
        return [goal]

    min_n, max_n = bounds["north"]
    min_e, max_e = bounds["east"]

    # Generate candidate clearance nodes around each obstacle
    nodes: list[tuple[float, float]] = [start, goal]
    for obs in obstacles:
        cn = float(obs["center_north_m"])
        ce = float(obs["center_east_m"])
        r_eff = float(obs["radius_m"]) + safety_margin_m
        clearance_dist = r_eff * 1.25
        num_angles = 16
        for k in range(num_angles):
            angle = 2.0 * math.pi * k / num_angles
            pn = cn + clearance_dist * math.cos(angle)
            pe = ce + clearance_dist * math.sin(angle)

            # Node must lie strictly inside search_area bounds
            if not (min_n <= pn <= max_n and min_e <= pe <= max_e):
                continue

            # Node must be strictly outside all other obstacles
            is_safe = True
            for other_obs in obstacles:
                if other_obs is obs:
                    continue
                o_cn = float(other_obs["center_north_m"])
                o_ce = float(other_obs["center_east_m"])
                o_reff = float(other_obs["radius_m"]) + safety_margin_m
                if math.hypot(pn - o_cn, pe - o_ce) <= o_reff:
                    is_safe = False
                    break
            if is_safe:
                nodes.append((pn, pe))

    num_nodes = len(nodes)
    if num_nodes > MAX_GRAPH_NODES:
        raise ContractValidationError(
            f"Visibility graph node count ({num_nodes}) exceeds maximum allowable limit of {MAX_GRAPH_NODES}"
        )

    adj: dict[int, list[tuple[int, float]]] = {i: [] for i in range(num_nodes)}
    for i in range(num_nodes):
        for j in range(i + 1, num_nodes):
            if _segment_clear_with_obstacles(nodes[i], nodes[j], obstacles, safety_margin_m):
                dist = math.hypot(nodes[i][0] - nodes[j][0], nodes[i][1] - nodes[j][1])
                adj[i].append((j, dist))
                adj[j].append((i, dist))

    # A* search from node 0 (start) to node 1 (goal)
    start_heuristic = math.hypot(start[0] - goal[0], start[1] - goal[1])
    pq: list[tuple[float, float, int, list[int]]] = [(start_heuristic, 0.0, 0, [0])]
    visited: dict[int, float] = {}
    best_path_indices: list[int] | None = None
    iterations = 0

    while pq and iterations < MAX_ASTAR_ITERATIONS:
        iterations += 1
        _, cost, u, path = heapq.heappop(pq)
        if u == 1:
            best_path_indices = path
            break
        if u in visited and visited[u] <= cost:
            continue
        visited[u] = cost

        for v, edge_dist in adj[u]:
            new_cost = cost + edge_dist
            if v not in visited or visited[v] > new_cost:
                h = math.hypot(nodes[v][0] - goal[0], nodes[v][1] - goal[1])
                heapq.heappush(pq, (new_cost + h, new_cost, v, path + [v]))

    if best_path_indices is None:
        return None

    # Path smoothing: remove redundant intermediate waypoints
    smoothed_path: list[tuple[float, float]] = [nodes[best_path_indices[0]]]
    curr_idx = 0
    while curr_idx < len(best_path_indices) - 1:
        furthest_reachable = curr_idx + 1
        for next_idx in range(len(best_path_indices) - 1, curr_idx, -1):
            if _segment_clear_with_obstacles(
                nodes[best_path_indices[curr_idx]],
                nodes[best_path_indices[next_idx]],
                obstacles,
                safety_margin_m,
            ):
                furthest_reachable = next_idx
                break
        smoothed_path.append(nodes[best_path_indices[furthest_reachable]])
        curr_idx = furthest_reachable

    # Return waypoints excluding start
    return smoothed_path[1:]


def _generate_obstacle_aware_route(
    min_north: float,
    max_north: float,
    min_east: float,
    max_east: float,
    down: float,
    curr_north: float,
    curr_east: float,
    lane_spacing_m: float,
    obstacles: Sequence[Mapping[str, float]],
    safety_margin_m: float,
) -> tuple[tuple[float, float, float], ...]:
    """Generate collision-free boustrophedon search route with detour waypoints.

    Fails explicitly with ContractValidationError if any promised lane waypoint or
    connecting route is obstructed by obstacles, preventing silent coverage gaps.
    """
    east_coords = _generate_axis_coordinates(min_east, max_east, lane_spacing_m, axis_name="east")

    # Determine optimal initial sweep direction based on current vehicle position
    dist_to_min_e = abs(curr_east - min_east)
    dist_to_max_e = abs(curr_east - max_east)
    if dist_to_max_e < dist_to_min_e:
        east_coords = tuple(reversed(east_coords))

    dist_to_min_n = abs(curr_north - min_north)
    dist_to_max_n = abs(curr_north - max_north)
    start_at_min_n = dist_to_min_n <= dist_to_max_n

    raw_targets: list[tuple[float, float]] = []
    for i, e in enumerate(east_coords):
        lane_starts_at_min = (i % 2 == 0) if start_at_min_n else (i % 2 != 0)
        p1 = (min_north, e) if lane_starts_at_min else (max_north, e)
        p2 = (max_north, e) if lane_starts_at_min else (min_north, e)
        raw_targets.append(p1)
        raw_targets.append(p2)

    bounds = {"north": (min_north, max_north), "east": (min_east, max_east)}
    curr_pt = (curr_north, curr_east)
    waypoints_3d: list[tuple[float, float, float]] = []

    for tgt in raw_targets:
        # Explicit failure if promised search lane waypoint is obstructed
        if not _point_clear_with_obstacles(tgt[0], tgt[1], obstacles, safety_margin_m):
            raise ContractValidationError(
                f"Promised search lane waypoint ({tgt[0]:.2f}, {tgt[1]:.2f}) is blocked by an obstacle "
                f"(safety margin {safety_margin_m:.2f}m); cannot execute promised full coverage"
            )

        detour_segment = _plan_detour(curr_pt, tgt, obstacles, bounds, safety_margin_m)
        if detour_segment is None:
            raise ContractValidationError(
                f"Cannot find safe collision-free detour from ({curr_pt[0]:.2f}, {curr_pt[1]:.2f}) to "
                f"waypoint ({tgt[0]:.2f}, {tgt[1]:.2f}) around obstacles; route cannot be completed"
            )

        for step in detour_segment:
            waypoints_3d.append((step[0], step[1], down))
        curr_pt = tgt

    if not waypoints_3d:
        raise ContractValidationError(
            "Configured obstacles completely block all safe search routes; no executable path"
        )

    if len(waypoints_3d) > MAX_PLANNED_WAYPOINTS:
        raise ContractValidationError(
            f"Generated route waypoint count ({len(waypoints_3d)}) exceeds maximum allowable limit of {MAX_PLANNED_WAYPOINTS}"
        )

    return tuple(waypoints_3d)


class ObstacleAwareSearchAgentV02:
    """Obstacle-aware boustrophedon search agent with A* detour planning for spatial search."""

    def __init__(
        self,
        lane_spacing_m: float = 2.0,
        safety_margin_m: float = 0.25,
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

        self._safety_margin_m = _finite_float(safety_margin_m, "safety_margin_m")
        if self._safety_margin_m < 0:
            raise ContractValidationError("safety_margin_m must be non-negative")

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
            r = _finite_float(obs["radius_m"], "obstacle radius_m")
            if r <= 0:
                raise ContractValidationError("obstacle radius_m must be strictly positive")
            validated_obstacles.append({"center_north_m": cn, "center_east_m": ce, "radius_m": r})

        if len(validated_obstacles) > MAX_OBSTACLES:
            raise ContractValidationError(
                f"Obstacle count ({len(validated_obstacles)}) exceeds maximum allowable limit of {MAX_OBSTACLES}"
            )
        self._obstacles: tuple[Mapping[str, float], ...] = tuple(validated_obstacles)

        # Internal state tracking
        self._report_attempts: dict[str, int] = {}
        self._waypoints: tuple[tuple[float, float, float], ...] = ()
        self._waypoint_index: int = 0
        self._closed: bool = False

    @property
    def lane_spacing_m(self) -> float:
        return self._lane_spacing_m

    @property
    def safety_margin_m(self) -> float:
        return self._safety_margin_m

    @property
    def obstacles(self) -> tuple[Mapping[str, float], ...]:
        return self._obstacles

    @property
    def waypoints(self) -> tuple[tuple[float, float, float], ...]:
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
        """Close agent and clear internal trajectory and states."""
        self._report_attempts.clear()
        self._waypoints = ()
        self._waypoint_index = 0
        self._closed = True

    def act(
        self, observation_or_snapshot: PlatformObservationV02 | EpisodeSnapshotV02
    ) -> ActionV02:
        """Compute next search, detour, or report action. Strictly single-vehicle."""
        if self._closed:
            raise RuntimeError("ObstacleAwareSearchAgentV02 is closed; cannot act")

        if isinstance(observation_or_snapshot, PlatformObservationV02):
            return self._compute_action(observation_or_snapshot)

        if isinstance(observation_or_snapshot, EpisodeSnapshotV02):
            if len(observation_or_snapshot.observations) != 1:
                raise ContractValidationError(
                    f"ObstacleAwareSearchAgentV02 is strictly a single-vehicle agent; "
                    f"received snapshot with {len(observation_or_snapshot.observations)} vehicles"
                )
            obs = next(iter(observation_or_snapshot.observations.values()))
            return self._compute_action(obs)

        raise ContractValidationError(
            f"Expected PlatformObservationV02 or EpisodeSnapshotV02, got {type(observation_or_snapshot).__name__}"
        )

    def _validate_search_area(self, area: object) -> tuple[float, float, float, float, float, float]:
        """Validate search_area schema and bound ordering strictly."""
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

        # Truth isolation: Agent must NEVER access private scenario truth
        if "truth" in state or "ground_truth" in state:
            raise ContractValidationError("truth leaked into Agent observation state")

        # Validate mandatory vehicle position coordinates
        for key in COORDINATES:
            if key not in state:
                raise ContractValidationError(f"observation state missing required coordinate '{key}'")
            val = state[key]
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
                raise ContractValidationError(f"observation state coordinate '{key}' must be a finite number")

        curr_pos = (float(state["north_m"]), float(state["east_m"]), float(state["down_m"]))

        # Inspect platform-visible accepted reports (echo confirmation)
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

        # Priority 1: Report detected target via REPORT channel
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

        # Priority 2: Execute obstacle-aware search and detour route
        if not self._waypoints:
            min_n, max_n, min_e, max_e, min_d, max_d = self._validate_search_area(state.get("search_area"))
            if self._flight_down_m is not None:
                if not (min_d <= self._flight_down_m <= max_d):
                    raise ContractValidationError(
                        f"flight_down_m ({self._flight_down_m}) is outside search_area down bounds [{min_d}, {max_d}]"
                    )
                flight_down = self._flight_down_m
            else:
                if not (min_d <= curr_pos[2] <= max_d):
                    raise ContractValidationError(
                        f"Current vehicle down position ({curr_pos[2]}) is outside search_area down bounds [{min_d}, {max_d}]"
                    )
                flight_down = curr_pos[2]

            self._waypoints = _generate_obstacle_aware_route(
                min_north=min_n,
                max_north=max_n,
                min_east=min_e,
                max_east=max_e,
                down=flight_down,
                curr_north=curr_pos[0],
                curr_east=curr_pos[1],
                lane_spacing_m=self._lane_spacing_m,
                obstacles=self._obstacles,
                safety_margin_m=self._safety_margin_m,
            )
            self._waypoint_index = 0

        # Advance waypoint index if within tolerance of current target waypoint
        while self._waypoint_index < len(self._waypoints):
            target_pt = self._waypoints[self._waypoint_index]
            dist = math.dist(curr_pos, target_pt)
            if dist <= self._waypoint_tolerance_m:
                self._waypoint_index += 1
            else:
                break

        # If all waypoints have been visited, hold current position
        if self._waypoint_index >= len(self._waypoints):
            hold_payload = {
                "north_m": curr_pos[0],
                "east_m": curr_pos[1],
                "down_m": curr_pos[2],
            }
            return ActionV02(
                f"search-hold-{observation.sequence}-{observation.vehicle_id}",
                observation.vehicle_id,
                ACTION_KIND,
                ActionChannel.CONTROL,
                ACTION_SCHEMA,
                hold_payload,
                self._action_deadline_s,
            )

        next_target = self._waypoints[self._waypoint_index]

        # Active pre-flight check: ensure the segment to next_target clears all obstacles
        if not _segment_clear_with_obstacles(
            curr_pos, next_target, self._obstacles, self._safety_margin_m
        ):
            # Attempt dynamic replan from current position to next_target
            min_n, max_n, min_e, max_e, _, _ = self._validate_search_area(state.get("search_area"))
            bounds = {"north": (min_n, max_n), "east": (min_e, max_e)}
            replan = _plan_detour(
                (curr_pos[0], curr_pos[1]),
                (next_target[0], next_target[1]),
                self._obstacles,
                bounds,
                self._safety_margin_m,
            )
            if replan:
                flight_down = self._flight_down_m if self._flight_down_m is not None else curr_pos[2]
                detour_wps = tuple((pt[0], pt[1], flight_down) for pt in replan)
                self._waypoints = (
                    self._waypoints[: self._waypoint_index]
                    + detour_wps
                    + self._waypoints[self._waypoint_index + 1 :]
                )
                next_target = self._waypoints[self._waypoint_index]
            else:
                raise ContractValidationError(
                    f"Planned flight segment from {curr_pos} to {next_target} violates obstacle safety boundary"
                )

        payload = {
            "north_m": next_target[0],
            "east_m": next_target[1],
            "down_m": next_target[2],
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


def create_agent(config: Mapping[str, Any] | None = None, context: Any = None) -> ObstacleAwareSearchAgentV02:
    """Component factory for drone.agent.spatial_obstacle_search/agent."""
    cfg = dict(config or {})
    return ObstacleAwareSearchAgentV02(
        lane_spacing_m=cfg.get("lane_spacing_m", 2.0),
        safety_margin_m=cfg.get("safety_margin_m", 0.25),
        min_confidence=cfg.get("min_confidence", 0.5),
        max_report_attempts=cfg.get("max_report_attempts", 3),
        waypoint_tolerance_m=cfg.get("waypoint_tolerance_m", 0.3),
        action_deadline_s=cfg.get("action_deadline_s", 1.0),
        flight_down_m=cfg.get("flight_down_m"),
        obstacles=cfg.get("obstacles"),
    )
