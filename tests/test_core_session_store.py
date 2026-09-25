from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from threading import Event
from unittest.mock import patch

from agents.mock import FixedMoveAgent, HoverAgent
from backends.mock import MockBackend
from contracts import (
    Action, ActionKind, BackendConfig, CleanupResult, PositionNed,
    TaskProgress, TaskSpec, TerminationReason,
)
from core.session import EpisodeSession, SessionError, SessionState
from core.runner import EpisodeRunner
from core.recorder import Recorder
from core.store import EpisodeStore, StoreError
from core.episode_lifecycle import EpisodeLifecycle
from evaluators.mock import ReachPointEvaluator
from tasks.mock import ReachPointTask


TARGET = PositionNed(5, 0, -2)


def spec(budget: float = 10) -> TaskSpec:
    return TaskSpec("session-test", "reach_point", budget, PositionNed(0, 0, 0),
                    "relative_to_home", {"target_position_ned": TARGET.to_dict()})


class CleanupFails(MockBackend):
    def cleanup(self) -> CleanupResult:
        raise RuntimeError("landing failed")


class ResetFails(MockBackend):
    def reset(self, config, task):
        raise RuntimeError("cannot reset")


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def session(self, backend=None, agent=None, **kwargs) -> EpisodeSession:
        return EpisodeSession(backend or MockBackend(), agent or FixedMoveAgent(TARGET),
                              ReachPointTask((5, 0, -2)), ReachPointEvaluator((5, 0, -2)),
                              self.root, **kwargs)

    def test_success_is_replayable_and_session_can_reset_again(self):
        from core.replay import read_episode

        session = self.session()
        session.reset(spec(), BackendConfig("mock", "drone-1"), episode_id="first")
        outcome = session.step()
        self.assertEqual(outcome.result.termination_reason, TerminationReason.SUCCESS)
        self.assertEqual(session.state, SessionState.STOPPED)
        self.assertEqual(read_episode(self.root / "first").result, outcome.result)
        self.assertEqual(EpisodeStore.read_result(self.root, "first")["termination_reason"], "success")
        session.reset(spec(), BackendConfig("mock", "drone-1"), episode_id="second")
        self.assertEqual(session.step().result.termination_reason, TerminationReason.SUCCESS)

    def test_legacy_runner_uses_recorder_subclass_hooks(self):
        class TrackingRecorder(Recorder):
            def __init__(self, root):
                super().__init__(root)
                self.calls = []

            def start(self, episode_id, task, backend, metadata=None):
                self.calls.append("start")
                return super().start(episode_id, task, backend, metadata)

            def event(self, event):
                self.calls.append("event")
                return super().event(event)

            def step(self, step):
                self.calls.append("step")
                return super().step(step)

            def finish(self, result):
                self.calls.append("finish")
                return super().finish(result)

        recorder = TrackingRecorder(self.root)
        runner = EpisodeRunner(MockBackend(), FixedMoveAgent(TARGET),
                               ReachPointTask((5, 0, -2)), ReachPointEvaluator((5, 0, -2)),
                               recorder)
        result = runner.run(spec(), BackendConfig("mock", "drone-1"), episode_id="tracked")
        self.assertEqual(result.termination_reason, TerminationReason.SUCCESS)
        self.assertEqual(recorder.calls[0], "start")
        self.assertEqual(recorder.calls[-1], "finish")
        self.assertIn("event", recorder.calls)
        self.assertIn("step", recorder.calls)

    def test_training_driver_and_read_only_result_processor(self):
        class TrainingDriver:
            def __init__(self, session):
                self.session = session

            def run(self):
                results = []
                for index, kind in enumerate((ActionKind.HOVER, ActionKind.MOVE_TO)):
                    episode_id = f"training-{index}"
                    initial = self.session.reset(
                        spec(), BackendConfig("mock", "drone-1"), episode_id=episode_id)
                    action = Action(f"training-action-{index}", kind, initial.vehicle_id, 5.0,
                                    TARGET if kind is ActionKind.MOVE_TO else None)
                    outcome = self.session.step(action)
                    self.assert_done(outcome)
                    results.append(outcome.result)
                return results

            @staticmethod
            def assert_done(outcome):
                if outcome.result is None:
                    raise AssertionError("one-step training episode did not terminate")

        class ResultProcessor:
            def __init__(self, root):
                self.root = root

            def summarize(self, episode_ids):
                results = [EpisodeStore.read_result(self.root, episode_id)
                           for episode_id in episode_ids]
                return {"attempts": len(results),
                        "successes": sum(result["success"] for result in results),
                        "reasons": [result["termination_reason"] for result in results]}

        results = TrainingDriver(self.session(max_steps=1)).run()
        self.assertEqual([result.termination_reason for result in results],
                         [TerminationReason.TIMEOUT, TerminationReason.SUCCESS])
        ids = [result.episode_id for result in results]
        before = [(self.root / episode_id / "result.json").read_bytes() for episode_id in ids]
        summary = ResultProcessor(self.root).summarize(ids)
        after = [(self.root / episode_id / "result.json").read_bytes() for episode_id in ids]
        self.assertEqual(summary, {"attempts": 2, "successes": 1,
                                   "reasons": ["timeout", "success"]})
        self.assertEqual(before, after)

    def test_context_emits_events_and_restricts_artifact_lifetime(self):
        from core.replay import read_episode

        session = self.session()
        session.reset(spec(), BackendConfig("mock", "drone-1"), episode_id="context")
        context = session.context
        self.assertEqual(context.episode_id, "context")
        self.assertEqual(context.artifacts.write_bytes("custom/data.bin", b"data"), "custom/data.bin")
        context.emit("sample", {"vehicle_id": "drone-1"})
        session.step()
        replay = read_episode(self.root / "context")
        self.assertTrue(any(event.kind == "plugin_event" for event in replay.events))
        with self.assertRaises(RuntimeError):
            context.artifacts.write_bytes("custom/late.bin", b"late")

    def test_task_failure_cancel_and_step_budget(self):
        session = self.session(agent=HoverAgent(), max_steps=1)
        session.reset(spec(), BackendConfig("mock", "drone-1"), episode_id="budget")
        self.assertEqual(session.step().result.termination_reason, TerminationReason.TIMEOUT)
        session.reset(spec(), BackendConfig("mock", "drone-1"), episode_id="cancel")
        self.assertEqual(session.stop().termination_reason, TerminationReason.CANCELLED)

    def test_total_and_action_timeouts(self):
        class Clock:
            value = 0.0

            def __call__(self):
                self.value += 0.1
                return self.value

        session = self.session(clock=Clock())
        session.reset(spec(0.05), BackendConfig("mock", "drone-1"), episode_id="total-timeout")
        self.assertEqual(session.step().result.termination_reason, TerminationReason.TIMEOUT)
        self.assertEqual(EpisodeStore.read_result(self.root, "total-timeout")["termination_reason"], "timeout")

        session = self.session(agent=FixedMoveAgent(TARGET, deadline_s=0.05), clock=Clock())
        session.reset(spec(), BackendConfig("mock", "drone-1"), episode_id="action-timeout")
        outcome = session.step()
        self.assertEqual(outcome.result.termination_reason, TerminationReason.TIMEOUT)
        self.assertEqual(outcome.step.execution.error.code, "action_deadline_exceeded")

    def test_programmatic_cancellation_before_and_during_action(self):
        session = self.session()
        session.reset(spec(), BackendConfig("mock", "drone-1"), episode_id="cancel-before")
        session.request_cancel()
        outcome = session.step()
        self.assertIsNone(outcome.step)
        self.assertEqual(outcome.result.termination_reason, TerminationReason.CANCELLED)
        self.assertTrue(outcome.result.cleanup.succeeded)

        signal = Event()

        class CancellingBackend(MockBackend):
            def execute(self, action):
                result = super().execute(action)
                signal.set()
                return result

        session = self.session(backend=CancellingBackend(), cancel_event=signal)
        session.reset(spec(), BackendConfig("mock", "drone-1"), episode_id="cancel-during")
        outcome = session.step()
        self.assertIsNotNone(outcome.step)
        self.assertEqual(outcome.result.termination_reason, TerminationReason.CANCELLED)
        self.assertEqual(EpisodeStore.read_result(self.root, "cancel-during")["termination_reason"], "cancelled")

    def test_keyboard_interrupt_in_hooks_still_records_result(self):
        class InterruptedReset(MockBackend):
            def reset(self, config, task):
                raise KeyboardInterrupt()

        session = self.session(backend=InterruptedReset())
        with self.assertRaises(SessionError) as raised:
            session.reset(spec(), BackendConfig("mock", "drone-1"), episode_id="interrupt-reset")
        self.assertEqual(raised.exception.result.termination_reason, TerminationReason.CANCELLED)
        self.assertEqual(EpisodeStore.read_result(self.root, "interrupt-reset")["termination_reason"],
                         "cancelled")

        class InterruptedTask(ReachPointTask):
            def update(self, step):
                raise KeyboardInterrupt()

        session = EpisodeSession(MockBackend(), FixedMoveAgent(TARGET), InterruptedTask((5, 0, -2)),
                                 ReachPointEvaluator((5, 0, -2)), self.root)
        session.reset(spec(), BackendConfig("mock", "drone-1"), episode_id="interrupt-task")
        self.assertEqual(session.step().result.termination_reason, TerminationReason.CANCELLED)

        class InterruptedCleanup(MockBackend):
            def cleanup(self):
                raise KeyboardInterrupt()

        session = self.session(backend=InterruptedCleanup())
        session.reset(spec(), BackendConfig("mock", "drone-1"), episode_id="interrupt-cleanup")
        result = session.step().result
        self.assertEqual(result.termination_reason, TerminationReason.SUCCESS)
        self.assertFalse(result.cleanup.succeeded)
        self.assertEqual(EpisodeStore.read_result(self.root, "interrupt-cleanup")["termination_reason"],
                         "success")

    def test_caller_metadata_cannot_override_record_identity(self):
        session = self.session()
        session.reset(spec(), BackendConfig("mock", "drone-1", connection={"token": "private"}),
                      episode_id="metadata", metadata={"platform_contract": "wrong",
                                                       "backend_connection_recording": "included"})
        session.stop()
        run = json.loads((self.root / "metadata" / "run.json").read_text(encoding="utf-8"))
        backend = json.loads((self.root / "metadata" / "backend.json").read_text(encoding="utf-8"))
        self.assertEqual(run["platform_contract"], spec().schema)
        self.assertEqual(run["backend_connection_recording"], "omitted")
        self.assertEqual(backend["connection"], {})

    def test_runtime_release_and_journal_failure(self):
        class Runtime:
            acquired = False
            released = False

            def acquire(self, episode_id, config):
                self.acquired = True
                return None

            def release(self, resource):
                self.released = True

        runtime = Runtime()
        session = self.session(runtime=runtime)
        session.reset(spec(), BackendConfig("mock", "drone-1"), episode_id="runtime")
        self.assertEqual(session.step().result.termination_reason, TerminationReason.SUCCESS)
        self.assertTrue(runtime.acquired and runtime.released)

        original_append = EpisodeStore.append

        def failed_append(store, path, value):
            if path == "trajectory.jsonl":
                store.failed = True
                raise StoreError("simulated journal failure")
            return original_append(store, path, value)

        session = self.session()
        with patch.object(EpisodeStore, "append", failed_append):
            session.reset(spec(), BackendConfig("mock", "drone-1"), episode_id="journal-fail")
            result = session.step().result
        self.assertEqual(result.termination_reason, TerminationReason.INITIALIZATION_ERROR)
        self.assertTrue((self.root / "journal-fail" / "INCOMPLETE").exists())
        with self.assertRaises(StoreError):
            EpisodeStore.read_result(self.root, "journal-fail")

    def test_lifecycle_cleanup_continues_when_event_sink_fails(self):
        calls = []

        def failed_sink(event):
            raise StoreError("event write failed")

        class FailingComponent:
            def close(self):
                calls.append("first")
                raise RuntimeError("close broke")

        class LaterComponent:
            def close(self):
                calls.append("later")

        class Runtime:
            def acquire(self, episode_id, config):
                calls.append("acquire")
                return "resource"

            def release(self, resource):
                calls.append("release")

            def close(self):
                calls.append("runtime.close")

        lifecycle = EpisodeLifecycle(failed_sink)
        lifecycle.begin()
        with self.assertRaises(StoreError):
            lifecycle.emit("runner", "ordinary_event")
        runtime = Runtime()
        with self.assertRaises(StoreError):
            lifecycle.acquire(runtime, "episode", object())
        errors = lifecycle.close((("first.close", FailingComponent()),
                                  ("later.close", LaterComponent())), runtime=runtime)
        self.assertEqual(errors, ("first.close: close broke",))
        self.assertEqual(calls, ["acquire", "first", "later", "release", "runtime.close"])

    def test_runtime_release_and_close_errors_are_both_recorded(self):
        calls = []

        class Runtime:
            def acquire(self, episode_id, config):
                return "resource"

            def release(self, resource):
                calls.append("release")
                raise RuntimeError("release broke")

            def close(self):
                calls.append("close")
                raise RuntimeError("close broke")

        lifecycle = EpisodeLifecycle(lambda event: None)
        lifecycle.begin()
        runtime = Runtime()
        lifecycle.acquire(runtime, "episode", object())
        self.assertEqual(lifecycle.close((), runtime=runtime),
                         ("runtime.release: release broke", "runtime.close: close broke"))
        self.assertEqual(calls, ["release", "close"])

    def test_reset_failure_has_result_and_cleanup_error_is_secondary(self):
        session = self.session(backend=ResetFails())
        with self.assertRaises(SessionError) as raised:
            session.reset(spec(), BackendConfig("mock", "drone-1"), episode_id="reset-fail")
        self.assertEqual(raised.exception.result.termination_reason, TerminationReason.INITIALIZATION_ERROR)
        self.assertEqual(EpisodeStore.read_result(self.root, "reset-fail")["termination_reason"],
                         "initialization_error")

        session = self.session(backend=CleanupFails())
        session.reset(spec(), BackendConfig("mock", "drone-1"), episode_id="cleanup-fail")
        result = session.step().result
        self.assertEqual(result.termination_reason, TerminationReason.SUCCESS)
        self.assertFalse(result.cleanup.succeeded)
        self.assertEqual(EpisodeStore.read_result(self.root, "cleanup-fail")["termination_reason"],
                         "success")

    def test_task_failure_precedes_cleanup_failure(self):
        class FailingTask(ReachPointTask):
            def update(self, step):
                return TaskProgress(True, False, TerminationReason.TASK_FAILED)

        session = EpisodeSession(CleanupFails(), FixedMoveAgent(TARGET),
                                 FailingTask((5, 0, -2)), ReachPointEvaluator((5, 0, -2)),
                                 self.root)
        session.reset(spec(), BackendConfig("mock", "drone-1"), episode_id="failed-task-cleanup")
        result = session.step().result
        self.assertEqual(result.termination_reason, TerminationReason.TASK_FAILED)
        self.assertFalse(result.cleanup.succeeded)
        self.assertEqual(EpisodeStore.read_result(self.root, "failed-task-cleanup")["termination_reason"],
                         "task_failed")

    def test_cancellation_at_budget_boundary_precedes_timeout(self):
        class Clock:
            value = 0.0

            def __call__(self):
                self.value += 1.0
                return self.value

        session = self.session(clock=Clock())
        session.reset(spec(0.5), BackendConfig("mock", "drone-1"), episode_id="cancel-budget")
        session.request_cancel()
        result = session.step().result
        self.assertEqual(result.termination_reason, TerminationReason.CANCELLED)

    def test_final_result_write_failure_leaves_incomplete_marker(self):
        original_atomic = EpisodeStore._atomic

        def fail_result_write(store, path, data):
            if path.name == "result.json":
                store.failed = True
                raise StoreError("simulated result write failure")
            return original_atomic(store, path, data)

        session = self.session()
        with patch.object(EpisodeStore, "_atomic", fail_result_write):
            session.reset(spec(), BackendConfig("mock", "drone-1"), episode_id="result-write-fail")
            with self.assertRaises(StoreError):
                session.step()
        self.assertEqual(session.state, SessionState.STOPPED)
        self.assertTrue(session.result.cleanup.succeeded)
        self.assertTrue((self.root / "result-write-fail" / "INCOMPLETE").exists())
        with self.assertRaises(StoreError):
            EpisodeStore.read_result(self.root, "result-write-fail")

    def test_recording_failure_termination_event_matches_result(self):
        original_append = EpisodeStore.append

        def fail_execution_event(store, path, value):
            if path == "events.jsonl" and value["kind"] == "execution":
                store.failed = True
                raise StoreError("simulated event write failure")
            return original_append(store, path, value)

        session = self.session()
        with patch.object(EpisodeStore, "append", fail_execution_event):
            session.reset(spec(), BackendConfig("mock", "drone-1"), episode_id="event-fail")
            result = session.step().result
        self.assertEqual(result.termination_reason, TerminationReason.INITIALIZATION_ERROR)
        events = [json.loads(line) for line in (self.root / "event-fail" / "events.jsonl")
                  .read_text(encoding="utf-8").splitlines()]
        terminations = [event for event in events if event["kind"] == "termination"]
        self.assertEqual(terminations[-1]["fields"]["reason"], result.termination_reason.value)
        self.assertTrue((self.root / "event-fail" / "INCOMPLETE").exists())

    def test_artifact_paths_and_incomplete_result(self):
        store = EpisodeStore(self.root)
        store.start("artifacts")
        self.assertEqual(store.write_bytes("sensors/frame.bin", b"abc"), "sensors/frame.bin")
        self.assertEqual((self.root / "artifacts" / "sensors" / "frame.bin").read_bytes(), b"abc")
        for path in ("../outside", "sensors/../../outside", "/outside", "C:/outside", "sensors\\outside", "result.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                store.write_bytes(path, b"x")
        with self.assertRaises(StoreError):
            EpisodeStore.read_result(self.root, "artifacts")
        store.finish({"episode_id": "artifacts", "termination_reason": "success"})
        self.assertFalse((self.root / "artifacts" / "INCOMPLETE").exists())
        self.assertEqual(EpisodeStore.read_result(self.root, "artifacts")["episode_id"], "artifacts")
        with self.assertRaises(StoreError):
            store.write_bytes("late.bin", b"late")
        with self.assertRaises(StoreError):
            store.append("events.jsonl", {"kind": "late"})
        with self.assertRaises(StoreError):
            store.finish({"episode_id": "artifacts"})

    def test_result_reader_rejects_symlink_escape_and_missing_result(self):
        store = EpisodeStore(self.root)
        directory = store.start("unsafe-read")
        (directory / "INCOMPLETE").unlink()
        with self.assertRaises(StoreError):
            EpisodeStore.read_result(self.root, "unsafe-read")
        outside = self.root / "outside.json"
        outside.write_text('{"episode_id": "unsafe-read"}', encoding="utf-8")
        try:
            (directory / "result.json").symlink_to(outside)
        except OSError:
            self.skipTest("symlink creation is unavailable")
        with self.assertRaises(StoreError):
            EpisodeStore.read_result(self.root, "unsafe-read")

    def test_failed_journal_retains_incomplete_marker(self):
        store = EpisodeStore(self.root)
        store.start("bad-write")
        (self.root / "bad-write" / "events.jsonl").unlink()
        (self.root / "bad-write" / "events.jsonl").mkdir()
        with self.assertRaises(StoreError):
            store.append("events.jsonl", {"kind": "event"})
        store.finish({"episode_id": "bad-write"})
        self.assertTrue((self.root / "bad-write" / "INCOMPLETE").exists())
        with self.assertRaises(StoreError):
            EpisodeStore.read_result(self.root, "bad-write")


if __name__ == "__main__":
    unittest.main()
