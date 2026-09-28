"""External v0.2 task, evaluator, agents, and benchmark for spatial Mock worlds."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from contracts.data_v02 import ActionChannel, ActionV02, EpisodeSnapshotV02, PlatformObservationV02


COMPONENT_PREFIX = "sample.spatial"
ACTION_KIND = "spatial/move-to"
ACTION_SCHEMA = "spatial.move-to/v1"
BENCHMARK_VERSION = "reach-point/v1"
COORDINATES = ("north_m", "east_m", "down_m")


def _position(value: object) -> tuple[float, float, float]:
    if not isinstance(value, Mapping):
        raise ValueError("position must be an NED object")
    coordinates = tuple(value.get(key) for key in COORDINATES)
    if any(isinstance(item, bool) or not isinstance(item, (int, float))
           or not math.isfinite(item) for item in coordinates):
        raise ValueError("position requires finite north_m, east_m, down_m")
    return tuple(float(item) for item in coordinates)


def _distance(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return math.dist(a, b)


def _vehicle_position(snapshot: EpisodeSnapshotV02) -> tuple[float, float, float] | None:
    observation = snapshot.observations.get("A")
    return None if observation is None else _position(observation.state)


class ReachPointTask:
    def __init__(self, tolerance_m: float) -> None:
        self.tolerance_m = tolerance_m

    def complete(self, snapshot: EpisodeSnapshotV02, truth: Mapping[str, object]) -> bool:
        position = _vehicle_position(snapshot)
        return position is not None and _distance(position, _position(truth["goal"])) <= self.tolerance_m

    def close(self) -> None:
        pass


class ReachPointEvaluator:
    def __init__(self, tolerance_m: float) -> None:
        self.tolerance_m = tolerance_m

    def evaluate(self, result: Mapping[str, object]) -> dict[str, float]:
        goal = _position(result["truth"]["goal"])
        trajectory = result["trajectory"]
        if not isinstance(trajectory, Sequence) or isinstance(trajectory, (str, bytes)):
            raise ValueError("trajectory must be a sequence")
        points: list[tuple[float, float, float]] = []
        for step in trajectory:
            if not isinstance(step, Mapping):
                raise ValueError("trajectory step must be an object")
            if not points:
                points.append(_position(step["before"]["observations"]["A"]["state"]))
            points.append(_position(step["after"]["observations"]["A"]["state"]))
        if not points:
            points.append(_position(result["final_snapshot"]["observations"]["A"]["state"]))
        final_distance = _distance(points[-1], goal)
        path_length = sum(_distance(first, second) for first, second in zip(points, points[1:]))
        success = result["status"] == "success" and final_distance <= self.tolerance_m
        return {
            "spatial/success_ratio": float(success),
            "spatial/final_distance_m": final_distance,
            "spatial/path_length_m": path_length,
        }

    def close(self) -> None:
        pass


class GoalAgent:
    def __init__(self, staged: bool) -> None:
        self.staged = staged

    def act(self, observation: PlatformObservationV02) -> ActionV02:
        goal = _position(observation.state["goal"])
        current = _position(observation.state)
        target = (tuple((a + b) / 2 for a, b in zip(current, goal))
                  if self.staged and observation.sequence == 0 else goal)
        return ActionV02(
            f"spatial-{observation.sequence}-{observation.vehicle_id}",
            observation.vehicle_id, ACTION_KIND, ActionChannel.CONTROL,
            ACTION_SCHEMA, dict(zip(COORDINATES, target)), 1.0,
        )

    def close(self) -> None:
        pass


class ReachPointBenchmark:
    def cases(self) -> tuple[dict[str, object], ...]:
        return tuple({
            "case_id": f"{BENCHMARK_VERSION}.seed-{seed}",
            "scenario_seed": seed,
            "task": f"{COMPONENT_PREFIX}/task.reach-point",
        } for seed in (7, 11, 19))

    def close(self) -> None:
        pass


class ReachPointProcessor:
    def process(self, read_result, episode_ids: Sequence[str]) -> dict[str, object]:
        results = [read_result(episode_id) for episode_id in episode_ids]
        count = len(results)
        successes = sum(result["metrics"]["spatial/success_ratio"] == 1.0
                        for result in results)
        return {
            "case_count": count,
            "success_count": successes,
            "success_ratio": successes / count if count else 0.0,
            "mean_final_distance_m": (
                sum(result["metrics"]["spatial/final_distance_m"] for result in results) / count
                if count else 0.0),
        }

    def close(self) -> None:
        pass


def _tolerance(config: Mapping[str, object]) -> float:
    value = config.get("tolerance_m", 0.25)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("tolerance_m must be finite and positive")
    return float(value)


def create_task(*, config, context):
    return ReachPointTask(_tolerance(config))


def create_evaluator(*, config, context):
    return ReachPointEvaluator(_tolerance(config))


def create_direct_agent(*, config, context):
    return GoalAgent(False)


def create_staged_agent(*, config, context):
    return GoalAgent(True)


def create_benchmark(*, config, context):
    return ReachPointBenchmark()


def create_processor(*, config, context):
    return ReachPointProcessor()


def register():
    return None
