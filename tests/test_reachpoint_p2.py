from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

from agents.reachpoint import FixedRouteAgent
from contracts import (
    Action, ActionKind, CleanupResult, ContractError, ContractValidationError,
    EpisodeResult, ExecutionResult, PlatformObservation, PositionNed, StepRecord,
    TaskProgress, TaskSpec, TerminationReason, time_budget_exhausted,
)
from evaluators.reachpoint import ReachPointEvaluator
from tasks.reachpoint import ReachPointTask


FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "reachpoint" / "scenarios.json").read_text(encoding="utf-8"))
HOME = PositionNed.from_dict(FIXTURE["home_ned"])
TARGET = PositionNed.from_dict(FIXTURE["target_ned"])


def spec(**parameters: object) -> TaskSpec:
    return TaskSpec(
        "reach-p2", "reach_point", 10.0, HOME, "relative_to_home",
        {"target_position_ned": TARGET.to_dict(), "tolerance_m": FIXTURE["tolerance_m"], **parameters},
    )


def observation(sequence: int, position: PositionNed, velocity=(0.0, 0.0, 0.0)) -> PlatformObservation:
    return PlatformObservation(sequence, "drone-1", position, velocity, sequence + 1)


class ReachPointP2Tests(unittest.TestCase):
    def test_fixture_outcomes_and_cleanup_remain_separate(self) -> None:
        for case in FIXTURE["cases"]:
            with self.subTest(case=case["name"]):
                task = ReachPointTask()
                before = observation(0, HOME)
                task.reset(spec(), before)
                action = Action("a-1", ActionKind.MOVE_TO, "drone-1", 5.0, TARGET)
                succeeded = case["execution_succeeded"]
                execution = ExecutionResult(
                    "a-1", True, True, succeeded, 2,
                    None if succeeded else ContractError("backend_error", "fixture command failed"),
                )
                after = observation(1, PositionNed.from_dict(case["after_ned"]))
                step = StepRecord(0, before, action, execution, after)
                progress = task.update(step)
                self.assertEqual((progress.done, progress.success), (case["expected_done"], case["expected_success"]))
                self.assertEqual(None if progress.reason is None else progress.reason.value, case["expected_reason"])
                metrics = ReachPointEvaluator.from_spec(spec()).evaluate((step,), progress)
                self.assertEqual(metrics["task_success"], float(progress.success))
                self.assertEqual(metrics["path_length_m"], math.dist(
                    (HOME.north_m, HOME.east_m, HOME.down_m),
                    (after.position_ned.north_m, after.position_ned.east_m, after.position_ned.down_m),
                ))
                if progress.done:
                    cleanup_error = case.get("cleanup_error")
                    cleanup = CleanupResult(True, case["cleanup_succeeded"], None if cleanup_error is None else ContractError(cleanup_error, "fixture cleanup failed"))
                    reason = progress.reason
                    result = EpisodeResult(case["name"], "reach-p2", reason, progress.success, after, cleanup, metrics)
                    self.assertEqual(result.termination_reason, reason)
                    self.assertEqual(result.cleanup.succeeded, case["cleanup_succeeded"])
                    if cleanup_error is not None:
                        self.assertTrue(result.success, "task outcome is preserved after cleanup failure")

    def test_timeout_fixture_uses_monotonic_budget_and_last_observation(self) -> None:
        before = observation(0, HOME)
        timeout = FIXTURE["timeout"]
        self.assertTrue(time_budget_exhausted(spec(), timeout["elapsed_s"]))
        result = EpisodeResult("timeout", "reach-p2", TerminationReason.TIMEOUT, False, before, CleanupResult(True, timeout["cleanup_succeeded"]))
        self.assertEqual(result.final_observation, before)
        self.assertEqual(result.termination_reason.value, timeout["termination_reason"])
        self.assertEqual(ReachPointEvaluator.from_spec(spec()).evaluate((), TaskProgress(False, False)), {"task_success": 0.0})

    def test_route_agent_advances_and_hover_confirms_arrival(self) -> None:
        midpoint = PositionNed(102.0, -20.0, 7.0)
        task_spec = spec(waypoints_ned=[midpoint.to_dict(), TARGET.to_dict()], require_hover_confirmation=True, max_arrival_speed_mps=0.1)
        agent = FixedRouteAgent()
        task = ReachPointTask()
        first = observation(0, HOME)
        agent.reset(task_spec, first)
        task.reset(task_spec, first)
        move_one = agent.act(first)
        self.assertEqual(move_one.target_position_ned, midpoint)
        middle = observation(1, midpoint)
        move_two = agent.act(middle)
        self.assertEqual(move_two.target_position_ned, TARGET)
        self.assertNotEqual(move_one.action_id, move_two.action_id)
        reached = observation(2, TARGET)
        move_step = StepRecord(1, middle, move_two, ExecutionResult(move_two.action_id, True, True, True, 3), reached)
        self.assertFalse(task.update(move_step).done)
        hover = agent.act(reached)
        self.assertEqual(hover.kind, ActionKind.HOVER)
        confirmed = observation(3, TARGET)
        hover_step = StepRecord(2, reached, hover, ExecutionResult(hover.action_id, True, True, True, 4), confirmed)
        self.assertTrue(task.update(hover_step).success)

    def test_arrival_speed_and_invalid_rules(self) -> None:
        before = observation(0, HOME)
        task = ReachPointTask()
        task.reset(spec(max_arrival_speed_mps=0.2), before)
        action = Action("move", ActionKind.MOVE_TO, "drone-1", 5.0, TARGET)
        fast = observation(1, TARGET, (1.0, 0.0, 0.0))
        step = StepRecord(0, before, action, ExecutionResult("move", True, True, True, 2), fast)
        self.assertFalse(task.update(step).done)
        for parameters in ({"tolerance_m": -1}, {"require_hover_confirmation": "yes"}, {"max_arrival_speed_mps": -1}):
            with self.subTest(parameters=parameters), self.assertRaises(ContractValidationError):
                ReachPointTask().reset(spec(**parameters), before)
        with self.assertRaises(ContractValidationError):
            FixedRouteAgent().reset(spec(waypoints_ned=[HOME.to_dict()]), before)


if __name__ == "__main__":
    unittest.main()
