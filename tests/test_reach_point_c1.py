"""C1 fixture acceptance: eight deterministic, simulator-free episodes."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from contracts import (
    Action, ActionKind, CleanupResult, ContractValidationError, EpisodeEvent,
    EventSource, ExecutionResult, PlatformObservation, PositionNed, StepRecord,
    TaskProgress, TaskSpec, TerminationReason,
)
from evaluators.reach_point import ReachPointEvaluator
from tasks.reach_point import ReachPointTask


ROOT = Path(__file__).resolve().parents[1]


def observation(sequence: int, north: float) -> PlatformObservation:
    return PlatformObservation(sequence, "drone-1", PositionNed(north, 0, 0),
                               (0, 0, 0), sequence + 1)


def build_case(base: dict, case: dict):
    source = json.loads(json.dumps(base))
    for key in ("require_return_home", "require_landing"):
        if key in case:
            source["parameters"][key] = case[key]
    spec = TaskSpec.from_dict(source)
    points = case["positions_north_m"]
    steps = []
    for i, north in enumerate(points[1:]):
        action = Action(f"a-{i}", ActionKind.MOVE_TO, "drone-1", 2,
                        PositionNed(10, 0, 0))
        execution = ExecutionResult(action.action_id, True, True,
                                    case.get("execution_succeeded", True), i + 1)
        steps.append(StepRecord(i, observation(i, points[i]), action,
                                execution, observation(i + 1, north)))
    events = tuple(EpisodeEvent(i, EventSource.BACKEND, kind, i + 1)
                   for i, kind in enumerate(case.get("events", [])))
    reason = TerminationReason(case["termination_reason"])
    progress = TaskProgress(True, reason is TerminationReason.SUCCESS, reason)
    return spec, observation(0, points[0]), tuple(steps), events, progress, reason


class ReachPointC1Tests(unittest.TestCase):
    def test_eight_fixtures_have_exact_results(self) -> None:
        fixture = json.loads((ROOT / "fixtures/reach_point/c1_cases.json").read_text(encoding="utf-8"))
        self.assertEqual(len(fixture["cases"]), 8)
        for case in fixture["cases"]:
            with self.subTest(case=case["name"]):
                spec, initial, steps, events, progress, reason = build_case(
                    fixture["base_task"], case)
                result = ReachPointEvaluator(spec).evaluate_episode(
                    initial, steps, tuple(case["step_elapsed_s"]), progress,
                    reason, events, CleanupResult(True, True))
                for key, expected in case["expected"].items():
                    actual = result.metrics[key] if key in result.metrics else getattr(result, key)
                    self.assertEqual(actual, expected, key)
                if steps:
                    protocol_metrics = ReachPointEvaluator(spec).evaluate(steps, progress)
                    self.assertEqual(protocol_metrics["goal_reached"], float(result.metrics["goal_reached"]))
                    self.assertEqual(protocol_metrics["goal_score"], result.goal_score)

    def test_task_uses_after_observation_not_command_target(self) -> None:
        fixture = json.loads((ROOT / "fixtures/reach_point/c1_cases.json").read_text(encoding="utf-8"))
        case = next(c for c in fixture["cases"] if c["name"] == "not_reached")
        spec, initial, steps, *_ = build_case(fixture["base_task"], case)
        task = ReachPointTask(); task.reset(spec, initial)
        self.assertFalse(task.update(steps[0]).success)
        task.close()

    def test_missing_collision_telemetry_is_not_zero_collisions(self) -> None:
        fixture = json.loads((ROOT / "fixtures/reach_point/c1_cases.json").read_text(encoding="utf-8"))
        case = fixture["cases"][0]
        spec, initial, steps, _, progress, reason = build_case(fixture["base_task"], case)
        result = ReachPointEvaluator(spec).evaluate_episode(
            initial, steps, tuple(case["step_elapsed_s"]), progress, reason,
            None, CleanupResult(True, True))
        self.assertIsNone(result.metrics["collision_count"])
        self.assertEqual(result.safety_status, "unavailable")
        self.assertFalse(result.rank_eligible)

    def test_rejects_false_success_and_non_monotonic_elapsed(self) -> None:
        fixture = json.loads((ROOT / "fixtures/reach_point/c1_cases.json").read_text(encoding="utf-8"))
        spec, initial, steps, events, _, _ = build_case(fixture["base_task"], fixture["cases"][0])
        evaluator = ReachPointEvaluator(spec)
        with self.assertRaises(ContractValidationError):
            evaluator.evaluate_episode(initial, steps, (2, 1),
                                       TaskProgress(True, True, TerminationReason.SUCCESS),
                                       TerminationReason.SUCCESS, events, CleanupResult(True, True))
        missed = build_case(fixture["base_task"], fixture["cases"][2])
        with self.assertRaises(ContractValidationError):
            ReachPointEvaluator(missed[0]).evaluate(
                missed[2], TaskProgress(True, True, TerminationReason.SUCCESS))

    def test_late_arrival_cannot_score_and_disqualification_is_distinct(self) -> None:
        fixture = json.loads((ROOT / "fixtures/reach_point/c1_cases.json").read_text(encoding="utf-8"))
        case = fixture["cases"][0]
        spec, initial, steps, events, progress, reason = build_case(fixture["base_task"], case)
        late = ReachPointEvaluator(spec).evaluate_episode(
            initial, steps, (2, 11), progress, reason, events, CleanupResult(True, True))
        self.assertTrue(late.metrics["goal_reached"])
        self.assertEqual(late.score, 0)
        self.assertFalse(late.rank_eligible)

        collision_case = fixture["cases"][5]
        spec_dict = json.loads(json.dumps(fixture["base_task"]))
        spec_dict["parameters"]["collision_policy"] = "disqualify"
        spec, initial, steps, events, progress, reason = build_case(spec_dict, collision_case)
        excluded = ReachPointEvaluator(spec).evaluate_episode(
            initial, steps, (2,), progress, reason, events, CleanupResult(True, True))
        self.assertEqual(excluded.score, 0)
        self.assertFalse(excluded.rank_eligible)

    def test_required_safe_finish_needs_events_and_cleanup(self) -> None:
        fixture = json.loads((ROOT / "fixtures/reach_point/c1_cases.json").read_text(encoding="utf-8"))
        case = dict(fixture["cases"][0], require_return_home=True,
                    require_landing=True,
                    events=["return_home_reached", "landed"])
        spec, initial, steps, events, progress, reason = build_case(fixture["base_task"], case)
        evaluator = ReachPointEvaluator(spec)
        passed = evaluator.evaluate_episode(initial, steps, (2, 4), progress, reason,
                                            events, CleanupResult(True, True))
        self.assertEqual(passed.score, 88)
        self.assertTrue(passed.metrics["return_home_success"])
        self.assertTrue(passed.metrics["landed_success"])
        failed = evaluator.evaluate_episode(initial, steps, (2, 4), progress, reason,
                                            events, CleanupResult(True, False))
        self.assertEqual(failed.score, 0)
        self.assertFalse(failed.rank_eligible)


if __name__ == "__main__":
    unittest.main()
