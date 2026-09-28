"""SearchTarget scoring policy fixtures, independent of a simulator or world generator."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from contracts.data_v02 import EpisodeSnapshotV02, PlatformObservationV02


PACK = Path(__file__).resolve().parents[1] / "examples" / "spatial_task_pack"
sys.path.insert(0, str(PACK / "src"))
from drone_spatial_tasks.search import (  # noqa: E402
    SearchAgent, SearchTargetEvaluator, SearchTargetTask,
)


TARGET = {"target": {"north_m": 3, "east_m": 4, "down_m": -2}, "target_id": "T1"}


def _snapshot(*, reports=(), detections=(), missing=False, sequence=0):
    return EpisodeSnapshotV02(sequence, {"A": PlatformObservationV02(
        sequence, "A", sequence + 1,
        {"north_m": 0, "east_m": 0, "down_m": -2,
         "search_area": {"north": [0, 6], "east": [0, 6], "down": [-4, -1]},
         "detections": list(detections), "reports": list(reports)},
        missing_sensors={"search-detector": "unavailable"} if missing else {},
    )})


def _report(north, east=4, *, target_id="T1"):
    return {"target_id": target_id, "north_m": north, "east_m": east, "down_m": -2}


def _result(status, *, reports=(), detections=(), missing=False):
    before = _snapshot(detections=detections, missing=missing).to_dict()
    after = _snapshot(reports=reports, detections=detections, missing=missing,
                      sequence=1).to_dict()
    actions = [{"kind": "spatial/report-target", "channel": "report",
                "payload": report} for report in reports]
    return {"status": status, "truth": TARGET, "final_snapshot": after,
            "trajectory": [{"before": before, "after": after, "actions": actions,
                            "outcomes": [{"succeeded": True} for _ in actions]}]}


def test_search_task_requires_matching_id_and_location():
    task = SearchTargetTask(0.75)
    assert not task.complete(_snapshot(reports=[_report(3, target_id="wrong")]), TARGET)
    assert not task.complete(_snapshot(reports=[_report(4)]), TARGET)
    assert task.complete(_snapshot(reports=[_report(3.2)]), TARGET)


def test_match_false_report_and_duplicate_scoring():
    evaluator = SearchTargetEvaluator(0.75, 100)
    metrics = evaluator.evaluate(_result("success", reports=[
        _report(1), _report(3.2), _report(3.1), _report(3, target_id="wrong")]))
    assert metrics["spatial/search_success_ratio"] == 1.0
    assert metrics["spatial/search_report_count"] == 4.0
    assert metrics["spatial/search_matched_count"] == 1.0
    assert metrics["spatial/search_false_positive_count"] == 3.0
    assert metrics["spatial/search_missed_target_count"] == 0.0
    assert metrics["spatial/search_localization_error_m"] == pytest.approx(0.1)

    wrong_id_only = evaluator.evaluate(_result("step_limit", reports=[
        _report(3, target_id="wrong")]))
    assert wrong_id_only["spatial/search_matched_count"] == 0.0
    assert wrong_id_only["spatial/search_false_positive_count"] == 1.0
    assert wrong_id_only["spatial/search_localization_error_m"] == 100.0
    assert wrong_id_only["spatial/search_localization_observed_ratio"] == 0.0


def test_missing_sensor_no_detection_and_timeout_are_distinct():
    evaluator = SearchTargetEvaluator(0.75, 100)
    missing = evaluator.evaluate(_result("step_limit", missing=True))
    no_detection = evaluator.evaluate(_result("timeout"))
    assert missing["spatial/search_initial_sensor_missing_ratio"] == 1.0
    assert missing["spatial/search_initial_no_detection_ratio"] == 0.0
    assert no_detection["spatial/search_initial_sensor_missing_ratio"] == 0.0
    assert no_detection["spatial/search_initial_no_detection_ratio"] == 1.0
    for metrics in (missing, no_detection):
        assert metrics["spatial/search_success_ratio"] == 0.0
        assert metrics["spatial/search_missed_target_count"] == 1.0
        assert metrics["spatial/search_false_positive_count"] == 0.0
        assert metrics["spatial/search_localization_error_m"] == 100.0
        assert metrics["spatial/search_localization_observed_ratio"] == 0.0


def test_report_agents_use_public_detection_and_offset_baseline():
    detection = {**_report(3.2), "confidence": 0.8}
    observation = _snapshot(detections=[detection]).observations["A"]
    confident = SearchAgent(False).act(observation)
    offset = SearchAgent(True).act(observation)
    assert confident.channel.value == offset.channel.value == "report"
    assert confident.payload["north_m"] == 3.2
    assert offset.payload["north_m"] == pytest.approx(1.2)
    assert SearchAgent(False).act(_snapshot(missing=True).observations["A"]).kind == "spatial/move-to"
