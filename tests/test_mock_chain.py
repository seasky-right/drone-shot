from __future__ import annotations

import unittest
import json
from pathlib import Path

from agents.mock import FixedMoveAgent
from backends.mock import MockBackend
from contracts import Action, BackendConfig, CleanupResult, ContractValidationError, EpisodeResult, ExecutionResult, PlatformContractException, PlatformObservation, PositionNed, StepRecord, TaskSpec, TerminationReason, time_budget_exhausted
from evaluators.mock import ReachPointEvaluator
from tasks.mock import ReachPointTask


def spec() -> TaskSpec:
    return TaskSpec("reach-fixture", "reach_point", 10, PositionNed(100, -20, 8), "relative_to_home")


class MockChainTests(unittest.TestCase):
    def test_shared_fixtures_describe_all_required_terminations(self) -> None:
        root = Path(__file__).resolve().parents[1] / "fixtures" / "episodes"
        loaded = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in root.glob("*.json")}
        self.assertEqual(set(loaded), {"success", "timeout", "backend_failure"})
        self.assertEqual({item["episode_result"]["termination_reason"] for item in loaded.values()}, {"success", "timeout", "backend_error"})
        for name, item in loaded.items():
            task = TaskSpec.from_dict(item["task_spec"])
            BackendConfig.from_dict(item["backend_config"])
            before = PlatformObservation.from_dict(item["observation_before"])
            result = EpisodeResult.from_dict(item["episode_result"])
            self.assertEqual(result.task_id, task.task_id)
            if name == "timeout":
                self.assertTrue(time_budget_exhausted(task, result.metrics["elapsed_s"]))
                self.assertEqual(result.final_observation, before)
                continue
            action = Action.from_dict(item["action"])
            execution = ExecutionResult.from_dict(item["execution"])
            after = PlatformObservation.from_dict(item["observation_after"])
            step = StepRecord(0, before, action, execution, after)
            self.assertEqual(result.final_observation, step.observation_after)
            progress = ReachPointTask((105, -20, 6)).update(step)
            self.assertEqual(progress.reason.value, result.termination_reason.value)

    def _step(self, backend: MockBackend, agent: FixedMoveAgent, task: ReachPointTask):
        initial = backend.reset(BackendConfig("mock", "drone-1"), spec())
        agent.reset(spec(), initial); task.reset(spec(), initial)
        action = agent.act(initial); execution = backend.execute(action); after = backend.observe()
        step = StepRecord(0, initial, action, execution, after)
        return step, task.update(step), backend

    def test_success_uses_after_observation_and_preserves_nonzero_home(self) -> None:
        target = PositionNed(105, -20, 6)
        step, progress, backend = self._step(MockBackend(), FixedMoveAgent(target), ReachPointTask((105, -20, 6)))
        self.assertEqual(step.observation_before.position_ned, PositionNed(100, -20, 8))
        self.assertEqual(step.observation_after.position_ned, target)
        self.assertTrue(progress.success)
        metrics = ReachPointEvaluator((105, -20, 6)).evaluate((step,), progress)
        result = EpisodeResult("e-success", spec().task_id, TerminationReason.SUCCESS, True, step.observation_after, backend.cleanup(), metrics)
        self.assertEqual(result.final_observation, step.observation_after)

    def test_backend_failure_keeps_post_action_observation_and_cleanup(self) -> None:
        step, progress, backend = self._step(MockBackend(fail_next_action=True), FixedMoveAgent(PositionNed(105, -20, 6)), ReachPointTask((105, -20, 6)))
        self.assertFalse(step.execution.succeeded)
        self.assertEqual(step.observation_after.sequence, 1, "failure still has a post-action sample")
        self.assertEqual(step.observation_after.position_ned, step.observation_before.position_ned)
        self.assertEqual(progress.reason, TerminationReason.BACKEND_ERROR)
        self.assertEqual(backend.cleanup(), CleanupResult(True, True))

    def test_timeout_has_final_observation_and_cleanup(self) -> None:
        backend = MockBackend(); initial = backend.reset(BackendConfig("mock", "drone-1"), spec())
        elapsed = spec().time_budget_s
        self.assertTrue(time_budget_exhausted(spec(), elapsed))
        result = EpisodeResult("e-timeout", spec().task_id, TerminationReason.TIMEOUT, False, initial, backend.cleanup(), {"elapsed_s": elapsed})
        self.assertEqual(result.final_observation, initial)

    def test_step_rejects_cross_vehicle_observations(self) -> None:
        backend = MockBackend(); before = backend.reset(BackendConfig("mock", "drone-1"), spec())
        action = FixedMoveAgent(PositionNed(105, -20, 6)); action.reset(spec(), before)
        command = action.act(before); execution = backend.execute(command); after = backend.observe()
        other_vehicle = type(after)(after.sequence, "drone-2", after.position_ned, after.velocity_ned_mps, after.wall_time_ns)
        with self.assertRaises(ContractValidationError):
            StepRecord(0, before, command, execution, other_vehicle)

    def test_close_is_idempotent_and_separate_from_cleanup(self) -> None:
        backend = MockBackend(); initial = backend.reset(BackendConfig("mock", "drone-1"), spec())
        self.assertEqual(backend.cleanup(), CleanupResult(True, True))
        self.assertEqual(backend.observe().position_ned, initial.position_ned, "cleanup does not release the session")
        backend.close(); backend.close()
        with self.assertRaises(PlatformContractException) as caught:
            backend.observe()
        self.assertEqual(caught.exception.error.code, "not_connected")
        action = Action("after-close", initial_action_kind(), "drone-1", 1, PositionNed(105, -20, 6))
        self.assertEqual(backend.execute(action).error.code, "not_connected")
        restarted = backend.reset(BackendConfig("mock", "drone-1"), spec())
        self.assertEqual((restarted.sequence, restarted.position_ned), (initial.sequence, initial.position_ned))

    def test_wrong_vehicle_is_rejected_without_moving_mock_state(self) -> None:
        backend = MockBackend(); before = backend.reset(BackendConfig("mock", "drone-1"), spec())
        action = Action("wrong-vehicle", initial_action_kind(), "drone-2", 1, PositionNed(105, -20, 6))
        result = backend.execute(action)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.error.code, "invalid_argument")
        self.assertEqual(backend.observe().position_ned, before.position_ned)


def initial_action_kind():
    from contracts import ActionKind
    return ActionKind.MOVE_TO
