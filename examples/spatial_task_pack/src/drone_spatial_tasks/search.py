"""Experimental SearchTarget policy for one hidden spatial target."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from contracts.data_v02 import ActionChannel, ActionV02, EpisodeSnapshotV02, PlatformObservationV02

from . import ACTION_KIND, ACTION_SCHEMA, COORDINATES, _distance, _position


REPORT_KIND = "spatial/report-target"
REPORT_SCHEMA = "spatial.report-target/v1"
TASK_ID = "sample.spatial/task.search-target"
POLICY_VERSION = "search-target/v0.1-experimental"


def _reports(snapshot: EpisodeSnapshotV02) -> Sequence[Mapping[str, object]]:
    observation = snapshot.observations.get("A")
    if observation is None:
        return ()
    reports = observation.state.get("reports", [])
    if not isinstance(reports, list):
        raise ValueError("reports must be an array")
    return reports


def _correct_id(report: Mapping[str, object], truth: Mapping[str, object]) -> bool:
    return report.get("target_id") == truth["target_id"]


class SearchTargetTask:
    def __init__(self, tolerance_m: float) -> None:
        self.tolerance_m = tolerance_m

    def complete(self, snapshot: EpisodeSnapshotV02, truth: Mapping[str, object]) -> bool:
        target = _position(truth["target"])
        return any(_correct_id(report, truth)
                   and _distance(_position(report), target) <= self.tolerance_m
                   for report in _reports(snapshot))

    def close(self) -> None:
        pass


class SearchTargetEvaluator:
    def __init__(self, tolerance_m: float, missing_penalty_m: float) -> None:
        self.tolerance_m = tolerance_m
        self.missing_penalty_m = missing_penalty_m

    def evaluate(self, result: Mapping[str, object]) -> dict[str, float]:
        target = _position(result["truth"]["target"])
        trajectory = result["trajectory"]
        if not isinstance(trajectory, Sequence) or isinstance(trajectory, (str, bytes)):
            raise ValueError("trajectory must be an array")
        accepted_reports = []
        for step in trajectory:
            for action, outcome in zip(step["actions"], step["outcomes"]):
                if (action["kind"] == REPORT_KIND and action["channel"] == "report"
                        and outcome["succeeded"] is True):
                    accepted_reports.append(action["payload"])
        distances = [_distance(_position(report), target)
                     for report in accepted_reports if _correct_id(report, result["truth"])]
        matched = int(any(distance <= self.tolerance_m for distance in distances))
        first_snapshot = (trajectory[0]["before"] if trajectory
                          else result["final_snapshot"])
        observation = first_snapshot["observations"]["A"]
        sensor_missing = "search-detector" in observation.get("missing_sensors", {})
        detections = observation["state"].get("detections", [])
        if not isinstance(detections, list):
            raise ValueError("detections must be an array")
        no_detection = not sensor_missing and not detections
        return {
            "spatial/search_success_ratio": float(
                matched and result["status"] == "success"),
            "spatial/search_report_count": float(len(accepted_reports)),
            "spatial/search_matched_count": float(matched),
            "spatial/search_missed_target_count": float(1 - matched),
            "spatial/search_false_positive_count": float(len(accepted_reports) - matched),
            "spatial/search_localization_error_m": (
                min(distances) if distances else self.missing_penalty_m),
            "spatial/search_localization_observed_ratio": float(bool(distances)),
            "spatial/search_initial_sensor_missing_ratio": float(sensor_missing),
            "spatial/search_initial_no_detection_ratio": float(no_detection),
        }

    def close(self) -> None:
        pass


class SearchAgent:
    def __init__(self, offset_report: bool) -> None:
        self.offset_report = offset_report

    def act(self, observation: PlatformObservationV02) -> ActionV02:
        detections = observation.state.get("detections", [])
        if not isinstance(detections, list):
            raise ValueError("detections must be an array")
        if detections:
            detection = max(detections, key=lambda item: item["confidence"])
            payload = dict(zip(COORDINATES, _position(detection)))
            if self.offset_report:
                north_low, north_high = observation.state["search_area"]["north"]
                midpoint = (north_low + north_high) / 2
                payload["north_m"] += 2.0 if payload["north_m"] <= midpoint else -2.0
            if "target_id" in detection:
                payload["target_id"] = detection["target_id"]
            return ActionV02(
                f"search-report-{observation.sequence}-{observation.vehicle_id}",
                observation.vehicle_id, REPORT_KIND, ActionChannel.REPORT,
                REPORT_SCHEMA, payload, 1.0,
            )
        current = _position(observation.state)
        area = observation.state.get("search_area", {})
        if isinstance(area, Mapping) and all(key in area for key in ("north", "east", "down")):
            north_lo, north_hi = area["north"]
            east_lo, east_hi = area["east"]
            down = (area["down"][0] + area["down"][1]) / 2
            north_mid = (north_lo + north_hi) / 2
            east_mid = (east_lo + east_hi) / 2
            waypoints = (
                (north_lo, east_lo, down), (north_lo, east_mid, down),
                (north_lo, east_hi, down), (north_mid, east_hi, down),
                (north_hi, east_hi, down), (north_hi, east_mid, down),
                (north_hi, east_lo, down), (north_mid, east_lo, down),
            )
            nearest = min(range(len(waypoints)),
                          key=lambda index: _distance(current, waypoints[index]))
            target = waypoints[(nearest + 1) % len(waypoints)]
        else:
            target = current
        return ActionV02(
            f"search-move-{observation.sequence}-{observation.vehicle_id}",
            observation.vehicle_id, ACTION_KIND, ActionChannel.CONTROL,
            ACTION_SCHEMA, dict(zip(COORDINATES, target)), 1.0,
        )

    def close(self) -> None:
        pass


class SearchTargetBenchmark:
    def cases(self) -> tuple[dict[str, object], ...]:
        return tuple({"case_id": f"{POLICY_VERSION}.seed-{seed}",
                      "scenario_seed": seed, "task": TASK_ID}
                     for seed in (7, 11, 19))

    def close(self) -> None:
        pass


class SearchTargetProcessor:
    def process(self, read_result, episode_ids: Sequence[str]) -> dict[str, object]:
        results = [read_result(episode_id) for episode_id in episode_ids]
        count = len(results)
        metric = lambda key: sum(result["metrics"][key] for result in results)
        return {
            "policy_version": POLICY_VERSION,
            "case_count": count,
            "success_count": int(metric("spatial/search_success_ratio")),
            "success_ratio": metric("spatial/search_success_ratio") / count if count else 0.0,
            "missed_target_count": int(metric("spatial/search_missed_target_count")),
            "false_positive_count": int(metric("spatial/search_false_positive_count")),
            "initial_sensor_missing_count": int(metric("spatial/search_initial_sensor_missing_ratio")),
            "initial_no_detection_count": int(metric("spatial/search_initial_no_detection_ratio")),
        }

    def close(self) -> None:
        pass


def _positive(config: Mapping[str, object], key: str, default: float) -> float:
    value = config.get(key, default)
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError(f"{key} must be finite and positive")
    return float(value)


def create_task(*, config, context):
    return SearchTargetTask(_positive(config, "tolerance_m", 0.75))


def create_evaluator(*, config, context):
    return SearchTargetEvaluator(_positive(config, "tolerance_m", 0.75),
                                 _positive(config, "missing_penalty_m", 100.0))


def create_confident_agent(*, config, context):
    return SearchAgent(False)


def create_offset_agent(*, config, context):
    return SearchAgent(True)


def create_benchmark(*, config, context):
    return SearchTargetBenchmark()


def create_processor(*, config, context):
    return SearchTargetProcessor()
