"""Contract-level checks for the independently installable spatial task Pack."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys

import pytest

from contracts.data_v02 import EpisodeSnapshotV02, PlatformObservationV02
from core.conformance import validate_component_cases
from core.plugins import PluginRegistry


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "examples" / "spatial_task_pack"
sys.path.insert(0, str(PACK / "src"))
from drone_spatial_tasks import (  # noqa: E402
    GoalAgent, ReachPointBenchmark, ReachPointEvaluator, ReachPointProcessor,
    ReachPointTask,
)


def _snapshot(north: float, east: float, down: float, *, sequence: int = 0):
    return EpisodeSnapshotV02(sequence, {"A": PlatformObservationV02(
        sequence, "A", sequence + 1,
        {"north_m": north, "east_m": east, "down_m": down,
         "goal": {"north_m": 3, "east_m": 4, "down_m": -1}},
    )})


def _result(status: str, positions: tuple[tuple[float, float, float], ...]):
    snapshots = [_snapshot(*position, sequence=index).to_dict()
                 for index, position in enumerate(positions)]
    return {
        "status": status, "truth": {"goal": {"north_m": 3, "east_m": 4, "down_m": -1}},
        "trajectory": [{"before": before, "after": after}
                       for before, after in zip(snapshots, snapshots[1:])],
        "final_snapshot": snapshots[-1],
    }


def test_reach_point_uses_truth_and_metre_tolerance():
    task = ReachPointTask(0.25)
    truth = {"goal": {"north_m": 3, "east_m": 4, "down_m": -1}}
    assert not task.complete(_snapshot(0, 0, 0), truth)
    assert task.complete(_snapshot(3, 4, -1), truth)
    assert task.complete(_snapshot(3.1, 4, -1), truth)
    assert not task.complete(_snapshot(3.3, 4, -1), truth)


def test_evaluator_scores_failure_and_geometric_path_without_mutation():
    evaluator = ReachPointEvaluator(0.25)
    result = _result("step_limit", ((0, 0, 0), (3, 0, 0), (3, 3, -1)))
    original = json.loads(json.dumps(result))
    metrics = evaluator.evaluate(result)
    assert result == original
    assert metrics["spatial/success_ratio"] == 0.0
    assert metrics["spatial/final_distance_m"] == 1.0
    assert metrics["spatial/path_length_m"] == pytest.approx(3 + math.sqrt(10))
    assert all(math.isfinite(value) for value in metrics.values())


def test_agents_read_public_goal_and_benchmark_cases_are_stable():
    observation = _snapshot(0, 0, 0).observations["A"]
    direct = GoalAgent(False).act(observation)
    staged = GoalAgent(True).act(observation)
    assert direct.payload == {"north_m": 3.0, "east_m": 4.0, "down_m": -1.0}
    assert staged.payload == {"north_m": 1.5, "east_m": 2.0, "down_m": -0.5}
    assert [case["scenario_seed"] for case in ReachPointBenchmark().cases()] == [7, 11, 19]


def test_processor_counts_completed_failure_in_denominator():
    results = {
        "one": {"metrics": {"spatial/success_ratio": 1.0, "spatial/final_distance_m": 0.0}},
        "two": {"metrics": {"spatial/success_ratio": 0.0, "spatial/final_distance_m": 2.0}},
    }
    summary = ReachPointProcessor().process(results.__getitem__, ("one", "two"))
    assert summary == {"case_count": 2, "success_count": 1,
                       "success_ratio": 0.5, "mean_final_distance_m": 1.0}


def test_static_manifest_and_component_conformance_cases():
    manifest = PACK / "src" / "drone_spatial_tasks" / "drone_plugin.json"
    original_path = list(sys.path)
    sys.path[:] = [path for path in sys.path if "spatial_task_pack\\src" not in path
                   and "spatial_world_pack\\src" not in path]
    try:
        registry = PluginRegistry.discover(manifest_paths=(manifest,))
    finally:
        sys.path[:] = original_path
    assert not registry.issues
    cases = json.loads((PACK / "component-cases.json").read_text(encoding="utf-8"))
    report = validate_component_cases(registry, cases)
    assert all(item["status"] == "passed" for item in report.values()), report
