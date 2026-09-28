"""Independent v0.2 spatial world fixture, generator, and no-simulator backend."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
import json
import math
import time
from typing import Mapping

from contracts.data_v02 import (
    ActionChannel, ActionV02, CapabilitySetV02, EpisodeSnapshotV02,
    PlatformObservationV02, ScenarioSpecV02,
)
from core.multi_vehicle import ActionOutcome


SCENARIO_ID = "sample.spatial-grid"
SEARCH_SCENARIO_ID = "sample.spatial-search"
ACTION_KIND = "spatial/move-to"
ACTION_SCHEMA = "spatial.move-to/v1"
REPORT_KIND = "spatial/report-target"
REPORT_SCHEMA = "spatial.report-target/v1"
POSITION_KEYS = ("north_m", "east_m", "down_m")


def register(*, config=None):
    return None


def _world() -> tuple[dict[str, object], str]:
    raw = files(__package__).joinpath("world.json").read_bytes()
    world = json.loads(raw)
    if world["world_id"] != SCENARIO_ID or world["coordinate_frame"] != "local_ned":
        raise ValueError("spatial world identity or frame mismatch")
    return world, sha256(raw).hexdigest()


def _position(site: object) -> dict[str, float]:
    if isinstance(site, Mapping):
        if set(site) != set(POSITION_KEYS):
            raise ValueError("position requires three NED coordinates")
        values = [site[key] for key in POSITION_KEYS]
    elif isinstance(site, (list, tuple)) and len(site) == 3:
        values = site
    else:
        raise ValueError("position requires three NED coordinates")
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) for value in values):
        raise ValueError("position coordinates must be finite numbers")
    return dict(zip(POSITION_KEYS, (float(value) for value in values)))


def _inside(world: Mapping[str, object], point: Mapping[str, float]) -> bool:
    bounds = world["public"]["bounds_m"]
    return all(bounds[axis][0] <= point[key] <= bounds[axis][1]
               for key, axis in zip(POSITION_KEYS, ("north", "east", "down")))


def _segment_clear(world: Mapping[str, object], start: Mapping[str, float],
                   goal: Mapping[str, float]) -> bool:
    if not _inside(world, start) or not _inside(world, goal):
        return False
    delta_n = goal["north_m"] - start["north_m"]
    delta_e = goal["east_m"] - start["east_m"]
    length_sq = delta_n * delta_n + delta_e * delta_e
    for obstacle in world["public"]["obstacles"]:
        center_n, center_e = obstacle["center_north_m"], obstacle["center_east_m"]
        projection = ((center_n - start["north_m"]) * delta_n
                      + (center_e - start["east_m"]) * delta_e) / length_sq if length_sq else 0.0
        fraction = max(0.0, min(1.0, projection))
        nearest_n = start["north_m"] + fraction * delta_n
        nearest_e = start["east_m"] + fraction * delta_e
        if math.hypot(nearest_n - center_n, nearest_e - center_e) <= obstacle["radius_m"]:
            return False
    return True


def feasible_pairs(world: Mapping[str, object]) -> tuple[tuple[dict[str, float], dict[str, float]], ...]:
    generation = world["generation"]
    pairs = []
    for raw_start in generation["spawn_sites"]:
        start = _position(raw_start)
        for raw_goal in generation["goal_sites"]:
            goal = _position(raw_goal)
            distance = math.dist(tuple(start.values()), tuple(goal.values()))
            if (distance >= generation["minimum_start_goal_distance_m"]
                    and _segment_clear(world, start, goal)):
                pairs.append((start, goal))
    return tuple(pairs)


def _instance_checksum(world_checksum: str, seed: int,
                       start: Mapping[str, float], goal: Mapping[str, float],
                       scenario_id: str = SCENARIO_ID) -> str:
    payload = {"scenario_id": scenario_id, "world_checksum": world_checksum, "seed": seed,
               "start": start, "goal": goal}
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"))
                  .encode("ascii")).hexdigest()


@dataclass(frozen=True)
class SpatialScenario:
    spec: ScenarioSpecV02
    truth: Mapping[str, object]

    def close(self) -> None:
        pass


class SpatialGenerator:
    def generate(self, seed: int) -> SpatialScenario:
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        world, world_checksum = _world()
        pairs = feasible_pairs(world)
        if not pairs:
            raise ValueError("world has no feasible spawn/goal pair")
        start, goal = pairs[seed % len(pairs)]
        checksum = _instance_checksum(world_checksum, seed, start, goal)
        initial = {**start, "goal": goal, "world_id": world["world_id"],
                   "world_version": world["version"], "world_checksum": world_checksum,
                   "instance_id": checksum[:16]}
        spec = ScenarioSpecV02(SCENARIO_ID, world["version"], checksum, {}, seed,
                               {"A": initial})
        return SpatialScenario(spec, {"goal": goal})

    def close(self) -> None:
        pass


class SearchGenerator:
    def generate(self, seed: int) -> SpatialScenario:
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        world, world_checksum = _world()
        pairs = feasible_pairs(world)
        # Classes 1/2/other exercise missing detector, no initial hit, and
        # imperfect initial hit while retaining deterministic valid placement.
        mode = "missing" if seed % 5 == 1 else "far" if seed % 5 == 2 else "near"
        if mode == "far":
            pairs = tuple(pair for pair in pairs
                          if math.dist(tuple(pair[0].values()), tuple(pair[1].values())) > 3)
        elif mode == "near":
            pairs = tuple(pair for pair in pairs
                          if math.dist(tuple(pair[0].values()), tuple(pair[1].values())) <= 3)
        if not pairs:
            raise ValueError("world has no feasible search placement")
        start, target = pairs[seed % len(pairs)]
        checksum = _instance_checksum(world_checksum, seed, start, target, SEARCH_SCENARIO_ID)
        initial = {**start, "world_id": world["world_id"],
                   "world_version": world["version"], "world_checksum": world_checksum,
                   "instance_id": checksum[:16], "search_area": world["public"]["bounds_m"]}
        spec = ScenarioSpecV02(SEARCH_SCENARIO_ID, world["version"], checksum, {},
                               seed, {"A": initial})
        return SpatialScenario(spec, {"target_id": "T1", "target": target})

    def close(self) -> None:
        pass


class SpatialMockBackend:
    def __init__(self) -> None:
        self.position: dict[str, float] | None = None
        self.goal: dict[str, float] | None = None
        self.sequence = 0
        self.closed = False

    def capabilities(self) -> CapabilitySetV02:
        return CapabilitySetV02((ACTION_KIND,), (), {}, "local_ned", ("wall",),
                                1, ("load", "reset"), True, True)

    def reset(self, scenario: ScenarioSpecV02,
              vehicle_ids: tuple[str, ...]) -> EpisodeSnapshotV02:
        if self.closed:
            raise RuntimeError("backend is closed")
        if scenario.scenario_id != SCENARIO_ID or vehicle_ids != ("A",):
            raise ValueError("backend requires sample.spatial-grid and vehicle A")
        world, world_checksum = _world()
        if scenario.version != world["version"] or scenario.seed is None:
            raise ValueError("unsupported world version or missing seed")
        initial = scenario.initial_states.get("A")
        if not isinstance(initial, Mapping):
            raise ValueError("missing vehicle A initial state")
        start = _position({key: initial[key] for key in POSITION_KEYS})
        goal = _position(initial.get("goal"))
        expected = SpatialGenerator().generate(scenario.seed).spec
        if (scenario.checksum != expected.checksum or initial != expected.initial_states["A"]
                or initial.get("world_checksum") != world_checksum
                or not _segment_clear(world, start, goal)):
            raise ValueError("scenario instance does not match feasible world generation")
        self.position, self.goal, self.sequence = start, goal, 0
        return self.observe()

    def execute(self, action: ActionV02) -> ActionOutcome:
        if self.closed or self.position is None:
            return ActionOutcome(action.action_id, action.vehicle_id, False, "backend is not active")
        if action.vehicle_id != "A":
            return ActionOutcome(action.action_id, action.vehicle_id, False, "unknown vehicle")
        if (action.kind != ACTION_KIND or action.channel is not ActionChannel.CONTROL
                or action.payload_schema != ACTION_SCHEMA):
            return ActionOutcome(action.action_id, action.vehicle_id, False, "unsupported action")
        try:
            target = _position(action.payload)
        except ValueError as exc:
            return ActionOutcome(action.action_id, action.vehicle_id, False, str(exc))
        world, _ = _world()
        if not _segment_clear(world, self.position, target):
            return ActionOutcome(action.action_id, action.vehicle_id, False,
                                 "movement crosses obstacle or world bounds")
        self.position = target
        self.sequence += 1
        return ActionOutcome(action.action_id, action.vehicle_id, True)

    def observe(self) -> EpisodeSnapshotV02:
        if self.closed or self.position is None or self.goal is None:
            raise RuntimeError("backend is not active")
        state = {**self.position, "goal": dict(self.goal)}
        observation = PlatformObservationV02(self.sequence, "A", time.time_ns(), state)
        return EpisodeSnapshotV02(self.sequence, {"A": observation})

    def close(self) -> None:
        self.position = None
        self.goal = None
        self.closed = True


class SearchMockBackend(SpatialMockBackend):
    def __init__(self) -> None:
        super().__init__()
        self.target: dict[str, float] | None = None
        self.detector_missing = False
        self.reports: list[dict[str, object]] = []
        self.action_handlers = {ActionChannel.REPORT: self.handle_report}

    def capabilities(self) -> CapabilitySetV02:
        return CapabilitySetV02((ACTION_KIND, REPORT_KIND), (), {}, "local_ned",
                                ("wall",), 1, ("load", "reset"), True, True)

    def reset(self, scenario: ScenarioSpecV02,
              vehicle_ids: tuple[str, ...]) -> EpisodeSnapshotV02:
        if self.closed:
            raise RuntimeError("backend is closed")
        if scenario.scenario_id != SEARCH_SCENARIO_ID or vehicle_ids != ("A",):
            raise ValueError("backend requires sample.spatial-search and vehicle A")
        world, world_checksum = _world()
        if scenario.version != world["version"] or scenario.seed is None:
            raise ValueError("unsupported world version or missing seed")
        expected = SearchGenerator().generate(scenario.seed)
        initial = scenario.initial_states.get("A")
        if (not isinstance(initial, Mapping)
                or scenario.checksum != expected.spec.checksum
                or initial != expected.spec.initial_states["A"]
                or initial["world_checksum"] != world_checksum):
            raise ValueError("search instance does not match feasible world generation")
        self.position = _position({key: initial[key] for key in POSITION_KEYS})
        self.target = _position(expected.truth["target"])
        self.goal = None
        self.detector_missing = scenario.seed % 5 == 1
        self.reports = []
        self.sequence = 0
        return self.observe()

    def handle_report(self, action: ActionV02) -> ActionOutcome:
        if self.closed or self.position is None:
            return ActionOutcome(action.action_id, action.vehicle_id, False, "backend is not active")
        if (action.vehicle_id != "A" or action.kind != REPORT_KIND
                or action.channel is not ActionChannel.REPORT
                or action.payload_schema != REPORT_SCHEMA):
            self.sequence += 1
            return ActionOutcome(action.action_id, action.vehicle_id, False, "unsupported report")
        try:
            if set(action.payload) - (set(POSITION_KEYS) | {"target_id"}):
                raise ValueError("unknown report field")
            estimate = _position({key: action.payload[key] for key in POSITION_KEYS})
            target_id = action.payload.get("target_id", "T1")
            if not isinstance(target_id, str) or not target_id:
                raise ValueError("invalid target ID")
        except (KeyError, ValueError) as exc:
            self.sequence += 1
            return ActionOutcome(action.action_id, action.vehicle_id, False, str(exc))
        self.reports.append({"target_id": target_id, **estimate})
        self.sequence += 1
        return ActionOutcome(action.action_id, action.vehicle_id, True)

    def observe(self) -> EpisodeSnapshotV02:
        if self.closed or self.position is None or self.target is None:
            raise RuntimeError("backend is not active")
        distance = math.dist(tuple(self.position.values()), tuple(self.target.values()))
        detections = []
        if not self.detector_missing and distance <= 3:
            detections.append({"target_id": "T1",
                               "north_m": self.target["north_m"] + 0.25,
                               "east_m": self.target["east_m"] - 0.25,
                               "down_m": self.target["down_m"],
                               "confidence": 0.8})
        world, _ = _world()
        state = {**self.position, "search_area": world["public"]["bounds_m"],
                 "detections": detections,
                 "reports": [dict(report) for report in self.reports]}
        missing = {"search-detector": "unavailable"} if self.detector_missing else {}
        observation = PlatformObservationV02(self.sequence, "A", time.time_ns(),
                                             state, missing_sensors=missing)
        return EpisodeSnapshotV02(self.sequence, {"A": observation})

    def close(self) -> None:
        super().close()
        self.target = None
        self.reports = []


def create_generator(*, config, context):
    return SpatialGenerator()


def create_backend(*, config, context):
    return SpatialMockBackend()


def create_search_generator(*, config, context):
    return SearchGenerator()


def create_search_backend(*, config, context):
    return SearchMockBackend()
