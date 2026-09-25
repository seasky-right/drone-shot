from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agents.mock import FixedMoveAgent, HoverAgent
from backends.mock import MockBackend
from contracts import BackendConfig, CleanupResult, ContractError, PositionNed, TaskSpec, TerminationReason
from core import EpisodeRunner, Recorder
from evaluators.mock import ReachPointEvaluator
from tasks.mock import ReachPointTask

TARGET = PositionNed(5, 0, -2)


def spec(budget: float = 10) -> TaskSpec:
    return TaskSpec("p2-test", "reach_point", budget, PositionNed(0, 0, 0),
                    "relative_to_home", {"target_position_ned": TARGET.to_dict()})


class CleanupFails(MockBackend):
    def cleanup(self) -> CleanupResult:
        raise RuntimeError("landing failed")


class AgentFails(FixedMoveAgent):
    def act(self, observation):
        raise RuntimeError("agent exploded")


class TaskFails(ReachPointTask):
    def update(self, step):
        raise RuntimeError("task exploded")


class EvaluatorFails(ReachPointEvaluator):
    def evaluate(self, steps, progress):
        raise RuntimeError("evaluator exploded")


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        self.value += 0.1
        return self.value


class RunnerTests(unittest.TestCase):
    def run_episode(self, backend=None, agent=None, task=None, evaluator=None, budget=10, clock=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        runner = EpisodeRunner(backend or MockBackend(), agent or FixedMoveAgent(TARGET),
                               task or ReachPointTask((5, 0, -2)),
                               evaluator or ReachPointEvaluator((5, 0, -2)),
                               Recorder(root), clock=clock or (lambda: 0.0))
        result = runner.run(spec(budget), BackendConfig("mock", "drone-1"), episode_id="test")
        directory = root / "test"
        self.assertTrue((directory / "task.json").exists())
        self.assertTrue((directory / "result.json").exists())
        self.assertEqual(json.loads((directory / "result.json").read_text(encoding="utf-8")), result.to_dict())
        steps = [json.loads(line) for line in (directory / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()]
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        return result, steps, events

    def test_success_records_last_after_observation_and_links_action(self):
        result, steps, events = self.run_episode()
        self.assertEqual(result.termination_reason, TerminationReason.SUCCESS)
        self.assertEqual(result.final_observation.sequence, 1)
        self.assertEqual(steps[-1]["observation_after"], result.final_observation.to_dict())
        self.assertEqual(steps[-1]["action"]["action_id"], steps[-1]["execution"]["action_id"])
        self.assertTrue(any(event["kind"] == "cleanup" for event in events))

    def test_backend_error_still_records_failed_step_and_cleanup(self):
        result, steps, _ = self.run_episode(backend=MockBackend(fail_next_action=True))
        self.assertEqual(result.termination_reason, TerminationReason.BACKEND_ERROR)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["observation_after"]["sequence"], 1)
        self.assertTrue(result.cleanup.succeeded)

    def test_backend_failure_precedes_task_and_evaluator_hooks(self):
        class LaterTaskFailure(ReachPointTask):
            def update(self, step):
                raise RuntimeError("later task error")

        result, steps, events = self.run_episode(
            backend=MockBackend(fail_next_action=True),
            task=LaterTaskFailure((5, 0, -2)))
        self.assertEqual(result.termination_reason, TerminationReason.BACKEND_ERROR)
        self.assertEqual(len(steps), 1)
        self.assertFalse(any(event["fields"].get("stage") == "task.update" for event in events))

    def test_agent_task_and_evaluator_errors_have_results(self):
        for key, component, expected, step_count in (
            ("agent", AgentFails(TARGET), TerminationReason.AGENT_ERROR, 0),
            ("task", TaskFails((5, 0, -2)), TerminationReason.TASK_ERROR, 1),
            ("evaluator", EvaluatorFails((5, 0, -2)), TerminationReason.EVALUATOR_ERROR, 1),
        ):
            with self.subTest(key=key):
                result, steps, events = self.run_episode(**{key: component})
                self.assertEqual(result.termination_reason, expected)
                self.assertEqual(len(steps), step_count)
                self.assertTrue(any(e["kind"] == "component_error" for e in events))

    def test_timeout_before_action_has_final_observation(self):
        result, steps, _ = self.run_episode(agent=HoverAgent(), budget=0.05, clock=Clock())
        self.assertEqual(result.termination_reason, TerminationReason.TIMEOUT)
        self.assertEqual(result.final_observation.sequence, 0)
        self.assertEqual(steps, [])

    def test_cleanup_failure_does_not_overwrite_task_outcome(self):
        result, _, events = self.run_episode(backend=CleanupFails())
        self.assertEqual(result.termination_reason, TerminationReason.SUCCESS)
        self.assertEqual(result.cleanup, CleanupResult(True, False, ContractError("RuntimeError", "landing failed")))
        self.assertTrue(any(e["kind"] == "cleanup_error" for e in events))


    def test_reset_failure_labels_synthetic_final_observation(self):
        class ResetFails(MockBackend):
            def reset(self, config, task):
                raise RuntimeError("cannot connect")
        result, steps, events = self.run_episode(backend=ResetFails())
        self.assertEqual(result.termination_reason, TerminationReason.INITIALIZATION_ERROR)
        self.assertEqual(result.final_observation.position_ned, spec().home_position_ned)
        self.assertIn("synthetic", result.final_observation.missing_sensors["__platform_state__"])
        self.assertEqual(steps, [])
        self.assertTrue(any(e["kind"] == "synthetic_final_observation" for e in events))

    def test_observe_failure_keeps_last_real_observation(self):
        class ObserveFails(MockBackend):
            def __init__(self):
                super().__init__()
                self.reads = 0
            def observe(self):
                self.reads += 1
                if self.reads == 3:
                    raise RuntimeError("sensor read failed")
                return super().observe()
        result, steps, _ = self.run_episode(backend=ObserveFails())
        self.assertEqual(result.termination_reason, TerminationReason.BACKEND_ERROR)
        self.assertEqual(result.final_observation.sequence, 0)
        self.assertEqual(steps, [])

    def test_recording_failure_leaves_incomplete_marker_and_failure_result(self):
        class FailingRecorder(Recorder):
            def _append(self, name, value):
                self.failed = True
                self.failure_message = "simulated write failure"
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        result = EpisodeRunner(MockBackend(), FixedMoveAgent(TARGET), ReachPointTask((5, 0, -2)),
                               ReachPointEvaluator((5, 0, -2)), FailingRecorder(root),
                               clock=lambda: 0.0).run(spec(), BackendConfig("mock", "drone-1"), episode_id="failed-record")
        self.assertEqual(result.termination_reason, TerminationReason.INITIALIZATION_ERROR)
        self.assertIsNone(result.record_path)
        self.assertTrue((root / "failed-record" / "INCOMPLETE").exists())
        self.assertTrue((root / "failed-record" / "result.json").exists())
    def test_time_budget_after_action_precedes_arrival_success(self):
        result, steps, _ = self.run_episode(budget=0.3, clock=Clock())
        self.assertEqual(result.termination_reason, TerminationReason.TIMEOUT)
        self.assertEqual(len(steps), 1)
        self.assertEqual(result.final_observation.position_ned, TARGET)

    def test_action_deadline_is_enforced_after_synchronous_call(self):
        result, steps, _ = self.run_episode(agent=FixedMoveAgent(TARGET, deadline_s=0.05), clock=Clock())
        self.assertEqual(result.termination_reason, TerminationReason.BACKEND_ERROR)
        self.assertEqual(steps[0]["execution"]["error"]["code"], "action_deadline_exceeded")
    def test_keyboard_interrupt_records_cancelled_and_cleans_up(self):
        class InterruptedAgent(FixedMoveAgent):
            def act(self, observation):
                raise KeyboardInterrupt()
        result, steps, events = self.run_episode(agent=InterruptedAgent(TARGET))
        self.assertEqual(result.termination_reason, TerminationReason.CANCELLED)
        self.assertEqual(steps, [])
        self.assertTrue(result.cleanup.succeeded)
        self.assertTrue(any(e["kind"] == "cancelled" for e in events))
    def test_backend_resources_use_recorded_episode_directory(self):
        class CapturingBackend(MockBackend):
            def reset(self, config, task):
                self.received_config = config
                return super().reset(config, task)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = CapturingBackend()
            caller_config = BackendConfig("mock", "drone-1", resource_root=str(root / "elsewhere"))
            runner = EpisodeRunner(
                backend, FixedMoveAgent(TARGET), ReachPointTask((5, 0, -2)),
                ReachPointEvaluator((5, 0, -2)), Recorder(root),
            )
            result = runner.run(spec(), caller_config, episode_id="recorded")
            episode_dir = (root / "recorded").resolve()
            self.assertTrue(result.success)
            self.assertEqual(backend.received_config.resource_root, str(episode_dir))
            recorded = json.loads((episode_dir / "backend.json").read_text(encoding="utf-8"))
            self.assertEqual(recorded["resource_root"], str(episode_dir))

if __name__ == "__main__":
    unittest.main()
