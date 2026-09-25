"""Shared lifecycle behavior for the v0.2 installed-style episode path."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from contracts.data_v02 import EpisodeSnapshotV02
from core.multi_vehicle import ActionOutcome
from core.plugin_cli import run_multi
from core.plugins import PluginRegistry
from core.store import EpisodeStore, StoreError


SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "external_plugin"


class MultiVehicleLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(SAMPLE / "src"))
        import drone_ext_sample
        cls.sample = drone_ext_sample

    @classmethod
    def tearDownClass(cls) -> None:
        sys.path.remove(str(SAMPLE / "src"))
        sys.modules.pop("drone_ext_sample", None)

    def registry(self) -> PluginRegistry:
        return PluginRegistry.discover(manifest_paths=(
            SAMPLE / "src" / "drone_ext_sample" / "drone_plugin.json",))

    def config(self) -> dict[str, object]:
        return json.loads((SAMPLE / "runtime.json").read_text(encoding="utf-8"))

    def test_runtime_and_subscriber_share_ordered_events(self) -> None:
        received: list[dict[str, object]] = []
        with TemporaryDirectory() as directory:
            result = run_multi(self.config(), Path(directory), registry=self.registry(),
                               on_event=received.append)
            self.assertTrue(result["success"])
            episode = Path(directory) / result["episode_id"]
            stored = [json.loads(line) for line in
                      (episode / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(stored, received)
            self.assertEqual([event["sequence"] for event in stored], list(range(len(stored))))
            kinds = [event["kind"] for event in stored]
            self.assertLess(kinds.index("runtime_acquired"), kinds.index("reset"))
            self.assertLess(kinds.index("runtime_released"), kinds.index("termination"))
            self.assertIn("sample.runtime.acquired",
                          [event["fields"].get("name") for event in stored])
            self.assertIn("sample.runtime.released",
                          [event["fields"].get("name") for event in stored])
            actions = [event for event in stored if event["kind"] == "action"]
            self.assertEqual({event["fields"]["vehicle_id"] for event in actions}, {"A", "B"})
            self.assertEqual(EpisodeStore.read_result(directory, result["episode_id"]), result)

    def test_runtime_acquire_failure_closes_and_preserves_incomplete(self) -> None:
        holder: list[object] = []

        class FailingRuntime(self.sample.MockRuntimeProvider):
            def acquire(self, episode_id, request):
                raise RuntimeError("resource unavailable")

            def close(self):
                self.closed = True

        def create_runtime(*, config, context):
            runtime = FailingRuntime(context)
            holder.append(runtime)
            return runtime

        with TemporaryDirectory() as directory, patch.object(self.sample, "create_runtime", create_runtime):
            with self.assertRaisesRegex(RuntimeError, "resource unavailable"):
                run_multi(self.config(), Path(directory), registry=self.registry())
            episode = Path(directory) / "sample-runtime-two-vehicle"
            self.assertTrue(holder[0].closed)
            self.assertTrue((episode / "INCOMPLETE").is_file())
            self.assertFalse((episode / "result.json").exists())
            events = (episode / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("runtime.acquire", events)
            self.assertNotIn('"kind": "runtime_released"', events)

    def test_runtime_release_failure_preserves_success_result(self) -> None:
        class FailingRuntime(self.sample.MockRuntimeProvider):
            def release(self, resource):
                raise RuntimeError("release failed")

        def create_runtime(*, config, context):
            return FailingRuntime(context)

        with TemporaryDirectory() as directory, patch.object(self.sample, "create_runtime", create_runtime):
            result = run_multi(self.config(), Path(directory), registry=self.registry())
            self.assertTrue(result["success"])
            self.assertIn("runtime.release: release failed", result["cleanup_errors"])
            self.assertEqual(EpisodeStore.read_result(directory, result["episode_id"]), result)
            self.assertFalse((Path(directory) / result["episode_id"] / "INCOMPLETE").exists())

    def test_cancel_and_timeout_have_rereadable_results(self) -> None:
        cancel = Event()
        with TemporaryDirectory() as directory:
            result = run_multi(self.config(), Path(directory), registry=self.registry(),
                               cancel_event=cancel,
                               on_event=lambda event: cancel.set() if event["kind"] == "reset" else None)
            self.assertEqual(result["status"], "cancelled")
            self.assertEqual(result["step_count"], 0)
            self.assertEqual(EpisodeStore.read_result(directory, result["episode_id"]), result)

        data = self.config()
        data["episode_id"] = "budget-timeout"
        data["time_budget_s"] = 1e-12
        with TemporaryDirectory() as directory:
            result = run_multi(data, Path(directory), registry=self.registry())
            self.assertEqual(result["status"], "timeout")
            self.assertEqual(EpisodeStore.read_result(directory, result["episode_id"]), result)

    def test_partial_failure_is_recorded_before_task_success(self) -> None:
        class FailingVehicle(self.sample.TwoVehicleMock):
            def execute(self, requested):
                if requested.vehicle_id == "B":
                    return ActionOutcome(requested.action_id, "B", False, "scripted failure")
                return super().execute(requested)

        with TemporaryDirectory() as directory, patch.object(
                self.sample, "create_backend", lambda *, config, context: FailingVehicle()):
            result = run_multi(self.config(), Path(directory), registry=self.registry())
            self.assertEqual(result["status"], "partial_failure")
            self.assertEqual(result["step_count"], 1)
            self.assertEqual(EpisodeStore.read_result(directory, result["episode_id"]), result)

    def test_all_vehicles_missing_at_reset_records_result_without_agent_action(self) -> None:
        created: dict[str, object] = {}
        class AllMissingBackend(self.sample.TwoVehicleMock):
            def reset(self, scenario, vehicle_ids):
                self.reset_vehicle_ids = tuple(vehicle_ids)
                return EpisodeSnapshotV02(0, {}, {
                    vehicle: "telemetry unavailable" for vehicle in vehicle_ids})
            def close(self):
                self.closed = True
                super().close()
        class AgentMustNotRun(self.sample.CentralMoveAgent):
            def act(self, snapshot):
                raise AssertionError("agent was called after all vehicles went missing")
            def close(self):
                self.closed = True
                super().close()
        def create_backend(*, config, context):
            created["backend"] = AllMissingBackend()
            return created["backend"]
        def create_agent(*, config, context):
            created["agent"] = AgentMustNotRun(5)
            return created["agent"]
        with TemporaryDirectory() as directory, patch.object(
                self.sample, "create_backend", create_backend), patch.object(
                self.sample, "create_agent", create_agent):
            result = run_multi(self.config(), Path(directory), registry=self.registry())
            self.assertEqual(result["status"], "partial_failure")
            self.assertEqual(result["step_count"], 0)
            self.assertEqual(result["final_snapshot"]["observations"], {})
            self.assertEqual(result["final_snapshot"]["missing_vehicles"], {
                "A": "telemetry unavailable", "B": "telemetry unavailable"})
            self.assertEqual(created["backend"].reset_vehicle_ids, ("A", "B"))
            self.assertTrue(created["backend"].closed)
            self.assertTrue(created["agent"].closed)
            episode = Path(directory) / result["episode_id"]
            self.assertFalse((episode / "INCOMPLETE").exists())
            events = (episode / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("sample.runtime.released", events)
            self.assertIn('"reason": "partial_failure"', events)
            self.assertNotIn('"kind": "action"', events)
            self.assertEqual(EpisodeStore.read_result(directory, result["episode_id"]), result)

    def test_component_and_write_failures_preserve_incomplete(self) -> None:
        class FailingTask(self.sample.SearchTask):
            def complete(self, snapshot, truth):
                raise RuntimeError("task failed")

        with TemporaryDirectory() as directory, patch.object(
                self.sample, "create_task", lambda *, config, context: FailingTask()):
            with self.assertRaisesRegex(RuntimeError, "task failed"):
                run_multi(self.config(), Path(directory), registry=self.registry())
            episode = Path(directory) / "sample-runtime-two-vehicle"
            self.assertTrue((episode / "INCOMPLETE").is_file())
            self.assertFalse((episode / "result.json").exists())
            events = (episode / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("episode.step", events)
            self.assertIn("sample.runtime.released", events)

        class FailingStore(EpisodeStore):
            def append(self, relative_path, value):
                if relative_path == "trajectory.jsonl":
                    self.failed = True
                    raise StoreError("trajectory write failed")
                return super().append(relative_path, value)

        with TemporaryDirectory() as directory, patch("core.plugin_cli.EpisodeStore", FailingStore):
            with self.assertRaisesRegex(StoreError, "trajectory write failed"):
                run_multi(self.config(), Path(directory), registry=self.registry())
            episode = Path(directory) / "sample-runtime-two-vehicle"
            self.assertTrue((episode / "INCOMPLETE").is_file())
            self.assertFalse((episode / "result.json").exists())

    def test_action_event_write_failure_still_releases_runtime_and_backend(self) -> None:
        created: dict[str, object] = {}

        class TrackingBackend(self.sample.TwoVehicleMock):
            def close(self):
                self.closed = True
                super().close()

        class TrackingRuntime(self.sample.MockRuntimeProvider):
            def release(self, resource):
                self.released = True
                super().release(resource)

            def close(self):
                self.closed = True
                super().close()

        class FailingStore(EpisodeStore):
            def append(self, relative_path, value):
                if relative_path == "events.jsonl" and value["kind"] == "action":
                    self.failed = True
                    raise StoreError("action event write failed")
                return super().append(relative_path, value)

        def create_backend(*, config, context):
            created["backend"] = TrackingBackend()
            return created["backend"]

        def create_runtime(*, config, context):
            created["runtime"] = TrackingRuntime(context)
            return created["runtime"]

        with (TemporaryDirectory() as directory,
              patch.object(self.sample, "create_backend", create_backend),
              patch.object(self.sample, "create_runtime", create_runtime),
              patch("core.plugin_cli.EpisodeStore", FailingStore)):
            with self.assertRaisesRegex(StoreError, "action event write failed"):
                run_multi(self.config(), Path(directory), registry=self.registry())
            episode = Path(directory) / "sample-runtime-two-vehicle"
            self.assertTrue(created["backend"].closed)
            self.assertTrue(created["runtime"].released)
            self.assertTrue(created["runtime"].closed)
            self.assertTrue((episode / "INCOMPLETE").is_file())
            self.assertFalse((episode / "result.json").exists())


if __name__ == "__main__":
    unittest.main()
