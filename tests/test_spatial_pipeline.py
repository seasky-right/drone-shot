"""Two external Packs compose into the same scored case set without a simulator."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from core.plugin_cli import run_benchmark
from core.plugins import PluginRegistry
from core.store import EpisodeStore


ROOT = Path(__file__).resolve().parents[1]
WORLD = ROOT / "examples" / "spatial_world_pack"
TASKS = ROOT / "examples" / "spatial_task_pack"


def _registry(monkeypatch):
    original_path = list(sys.path)
    sys.path[:] = [path for path in sys.path if "spatial_task_pack\\src" not in path
                   and "spatial_world_pack\\src" not in path]
    try:
        registry = PluginRegistry.discover(manifest_paths=(
            WORLD / "src" / "drone_spatial_world" / "drone_plugin.json",
            TASKS / "src" / "drone_spatial_tasks" / "drone_plugin.json",
        ))
    finally:
        sys.path[:] = original_path
    assert not registry.issues
    monkeypatch.syspath_prepend(str(WORLD / "src"))
    monkeypatch.syspath_prepend(str(TASKS / "src"))
    return registry


def _config(agent: str):
    return json.loads((TASKS / f"sample-benchmark-{agent}.json").read_text(encoding="utf-8"))


def test_two_agents_receive_identical_cases_and_scores_are_readable(tmp_path, monkeypatch):
    registry = _registry(monkeypatch)
    direct_root, staged_root = tmp_path / "direct", tmp_path / "staged"
    direct = run_benchmark(_config("direct"), direct_root, registry=registry)
    staged = run_benchmark(_config("staged"), staged_root, registry=registry)

    assert direct["case_count"] == staged["case_count"] == 3
    assert direct["success_count"] == staged["success_count"] == 3
    assert [case["case_id"] for case in direct["cases"]] == [
        case["case_id"] for case in staged["cases"]]
    assert [case["seed"] for case in direct["cases"]] == [7, 11, 19]
    assert EpisodeStore.read_result(direct_root / "benchmarks", direct["episode_id"]) == direct
    assert EpisodeStore.read_result(staged_root / "benchmarks", staged["episode_id"]) == staged

    for first_case, second_case in zip(direct["cases"], staged["cases"]):
        first = EpisodeStore.read_result(direct_root, first_case["episode_id"])
        second = EpisodeStore.read_result(staged_root, second_case["episode_id"])
        assert first["scenario"]["checksum"] == second["scenario"]["checksum"]
        assert first["scenario"]["initial_states"] == second["scenario"]["initial_states"]
        assert first["metrics"]["spatial/success_ratio"] == 1.0
        assert second["metrics"]["spatial/success_ratio"] == 1.0
        assert first["metrics"]["spatial/final_distance_m"] == pytest.approx(0.0)
        assert second["metrics"]["spatial/final_distance_m"] == pytest.approx(0.0)
        assert first["step_count"] == 1
        assert second["step_count"] == 2
        for root, result in ((direct_root, first), (staged_root, second)):
            assert "truth" not in result
            assert "artifact_root" not in result
            trajectory = (root / result["episode_id"] / "trajectory.jsonl").read_text(encoding="utf-8")
            assert '"truth"' not in trajectory
            assert '"ground_truth"' not in trajectory
            assert '"goal"' in trajectory  # The scenario deliberately makes this goal public.


def test_staged_agent_step_limit_is_scored_as_failure(tmp_path, monkeypatch):
    config = _config("staged")
    config["max_steps"] = 1
    summary = run_benchmark(config, tmp_path, registry=_registry(monkeypatch))
    assert summary["case_count"] == 3
    assert summary["success_count"] == 0
    assert summary["processed"]["case_count"] == 3
    assert summary["processed"]["success_ratio"] == 0.0
    assert all(case["status"] == "step_limit" for case in summary["cases"])
    assert all(case["metrics"]["spatial/success_ratio"] == 0.0 for case in summary["cases"])
    assert all(case["metrics"]["spatial/final_distance_m"] > 0 for case in summary["cases"])


def test_search_agents_compare_same_hidden_instances_and_count_failures(tmp_path, monkeypatch):
    registry = _registry(monkeypatch)
    confident_config = json.loads((TASKS / "sample-benchmark-search.json").read_text(encoding="utf-8"))
    offset_config = json.loads((TASKS / "sample-benchmark-search-offset.json").read_text(encoding="utf-8"))
    confident_root, offset_root = tmp_path / "confident", tmp_path / "offset"
    confident = run_benchmark(confident_config, confident_root, registry=registry)
    offset = run_benchmark(offset_config, offset_root, registry=registry)

    assert confident["case_count"] == offset["case_count"] == 3
    assert [case["case_id"] for case in confident["cases"]] == [
        case["case_id"] for case in offset["cases"]]
    assert [case["seed"] for case in confident["cases"]] == [7, 11, 19]
    assert confident["processed"]["case_count"] == offset["processed"]["case_count"] == 3
    assert confident["processed"]["initial_sensor_missing_count"] == 1
    assert confident["processed"]["initial_no_detection_count"] == 1
    assert offset["processed"]["initial_sensor_missing_count"] == 1
    assert offset["processed"]["initial_no_detection_count"] == 1
    assert EpisodeStore.read_result(confident_root / "benchmarks", confident["episode_id"]) == confident
    assert EpisodeStore.read_result(offset_root / "benchmarks", offset["episode_id"]) == offset

    for first_case, second_case in zip(confident["cases"], offset["cases"]):
        first = EpisodeStore.read_result(confident_root, first_case["episode_id"])
        second = EpisodeStore.read_result(offset_root, second_case["episode_id"])
        assert first["scenario"]["checksum"] == second["scenario"]["checksum"]
        assert first["scenario"]["initial_states"] == second["scenario"]["initial_states"]
        assert "target" not in first["scenario"]["initial_states"]["A"]
        for root, result in ((confident_root, first), (offset_root, second)):
            assert "truth" not in result
            record = (root / result["episode_id"] / "trajectory.jsonl").read_text(encoding="utf-8")
            assert '"truth"' not in record
            assert '"ground_truth"' not in record

    confident_near = confident["cases"][2]["metrics"]
    offset_near = offset["cases"][2]["metrics"]
    assert confident_near["spatial/search_success_ratio"] == 1.0
    assert confident_near["spatial/search_localization_error_m"] < 0.75
    assert offset_near["spatial/search_success_ratio"] == 0.0
    assert offset_near["spatial/search_false_positive_count"] >= 1.0
    assert offset_near["spatial/search_missed_target_count"] == 1.0
    assert confident["cases"][1]["metrics"]["spatial/search_initial_sensor_missing_ratio"] == 1.0
    assert confident["cases"][1]["metrics"]["spatial/search_missed_target_count"] == 1.0
