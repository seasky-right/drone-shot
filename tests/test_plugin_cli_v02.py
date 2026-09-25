"""No-simulator coverage of the installed-style v0.2 execution path."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from core.plugin_cli import _preflight, _runtime_negative_checks, main, run_multi
from core.plugins import PluginRegistry, PluginRegistryError
from core.cli import build_runner
from core.recorder import Recorder
from core.store import EpisodeStore
from contracts import BackendConfig, TaskSpec


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "external_plugin"


class PluginCliV02Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SAMPLE / "src"))

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(SAMPLE / "src"))
        sys.modules.pop("drone_ext_sample", None)

    def registry(self):
        return PluginRegistry.discover(
            manifest_paths=(SAMPLE / "src" / "drone_ext_sample" / "drone_plugin.json",))

    def test_dual_vehicle_result_is_reloaded_with_extension_and_event(self):
        data = json.loads((SAMPLE / "sample.json").read_text(encoding="utf-8"))
        with TemporaryDirectory() as directory:
            result = run_multi(data, Path(directory), registry=self.registry())
            self.assertTrue(result["success"])
            self.assertEqual(result["step_count"], 1)
            reread = EpisodeStore.read_result(directory, result["episode_id"])
            self.assertEqual(reread, result)
            self.assertEqual(set(reread["final_snapshot"]["observations"]), {"A", "B"})
            episode = Path(directory) / result["episode_id"]
            self.assertFalse((episode / "INCOMPLETE").exists())
            self.assertTrue((episode / "metadata" / "scenario.json").is_file())
            self.assertIn("sample.agent.loaded",
                          (episode / "events.jsonl").read_text(encoding="utf-8"))
            snapshot = (episode / "run.json").read_text(encoding="utf-8")
            self.assertIn("[REDACTED]", snapshot)
            self.assertNotIn("sample-only-secret", snapshot)

    def test_invalid_config_is_rejected_before_factory_import(self):
        data = json.loads((SAMPLE / "invalid-config.json").read_text(encoding="utf-8"))
        sys.modules.pop("drone_ext_sample", None)
        with self.assertRaises(PluginRegistryError) as caught:
            _preflight(self.registry(), data)
        self.assertEqual(caught.exception.issues[0].code, "invalid_config")
        self.assertNotIn("drone_ext_sample", sys.modules)

    def test_one_scenario_combines_with_search_and_mapping_tasks(self):
        with TemporaryDirectory() as directory:
            search = json.loads((SAMPLE / "sample.json").read_text(encoding="utf-8"))
            mapping = json.loads((SAMPLE / "mapping.json").read_text(encoding="utf-8"))
            self.assertEqual(search["components"]["scenario"], mapping["components"]["scenario"])
            self.assertTrue(run_multi(search, Path(directory), registry=self.registry())["success"])
            self.assertTrue(run_multi(mapping, Path(directory), registry=self.registry())["success"])

    def test_declared_capacity_is_checked_before_factory_import(self):
        data = json.loads((SAMPLE / "sample.json").read_text(encoding="utf-8"))
        data["vehicles"].append("C")
        sys.modules.pop("drone_ext_sample", None)
        with self.assertRaises(PluginRegistryError) as caught:
            _preflight(self.registry(), data)
        self.assertEqual(caught.exception.issues[0].code, "insufficient_capacity")
        self.assertNotIn("drone_ext_sample", sys.modules)

    def test_unsupported_action_preserves_legacy_preflight_code(self):
        data = json.loads((SAMPLE / "sample.json").read_text(encoding="utf-8"))
        data["required_action_kinds"] = ["sample/unknown"]
        sys.modules.pop("drone_ext_sample", None)
        with self.assertRaises(PluginRegistryError) as caught:
            _preflight(self.registry(), data)
        self.assertEqual(caught.exception.issues[0].code, "unsupported_action")
        self.assertNotIn("drone_ext_sample", sys.modules)

    def test_malformed_required_action_gets_structured_error(self):
        data = json.loads((SAMPLE / "sample.json").read_text(encoding="utf-8"))
        data["required_action_kinds"] = [{}]
        with self.assertRaises(PluginRegistryError) as caught:
            _preflight(self.registry(), data)
        self.assertEqual(caught.exception.issues[0].code, "invalid_capability_requirement")

    def test_backend_enable_guard_runs_before_factory_import(self):
        data = json.loads((SAMPLE / "sample.json").read_text(encoding="utf-8"))
        manifest = json.loads((SAMPLE / "src" / "drone_ext_sample" /
                               "drone_plugin.json").read_text(encoding="utf-8"))
        manifest["components"][0]["capabilities"]["requires_explicit_enable"] = True
        with TemporaryDirectory() as directory:
            path = Path(directory) / "drone_plugin.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            registry = PluginRegistry.discover(manifest_paths=(path,))
            sys.modules.pop("drone_ext_sample", None)
            with self.assertRaises(PluginRegistryError) as caught:
                _preflight(registry, data)
            self.assertEqual(caught.exception.issues[0].code, "explicit_enable_required")
            self.assertNotIn("drone_ext_sample", sys.modules)
            self.assertTrue(_preflight(registry, data, enable_backend=True))

    def test_run_multi_command_accepts_installed_style_config(self):
        with TemporaryDirectory() as directory, redirect_stdout(StringIO()) as printed:
            code = main(["run-multi", str(SAMPLE / "mapping.json"),
                         "--output", directory, "--manifest",
                         str(SAMPLE / "src" / "drone_ext_sample" / "drone_plugin.json")])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(printed.getvalue())["success"])

    def test_validate_plugin_checks_runtime_rejection_paths(self):
        with TemporaryDirectory() as directory, redirect_stdout(StringIO()) as printed:
            command = ["validate-plugin", "--sample", str(SAMPLE / "sample.json"),
                       "--output", directory, "--manifest",
                       str(SAMPLE / "src" / "drone_ext_sample" / "drone_plugin.json")]
            self.assertEqual(main(command), 0)
            self.assertEqual(main(command), 0)
            first, repeated = (json.loads(line) for line in printed.getvalue().splitlines())
            report = first
            self.assertTrue(report["valid"])
            self.assertEqual(report["checks"], {
                "artifact_path_rejection": True,
                "action_schema_rejection": True,
                "failure_incomplete_record": True,
                "result_reread": True,
                "truth_field_rejection": True,
            })
            self.assertEqual(report["skipped_checks"], [])
            self.assertNotEqual(first["sample"]["episode_id"], repeated["sample"]["episode_id"])
            self.assertEqual(first["sample"]["episode_id"], "sample-two-vehicle")
            self.assertEqual({path.name for path in Path(directory).iterdir()},
                             {first["sample"]["episode_id"], repeated["sample"]["episode_id"]})

    def test_explicit_backend_skips_repeated_runtime_probe(self):
        data = json.loads((SAMPLE / "sample.json").read_text(encoding="utf-8"))
        manifest = json.loads((SAMPLE / "src" / "drone_ext_sample" /
                               "drone_plugin.json").read_text(encoding="utf-8"))
        manifest["components"][0]["capabilities"]["requires_explicit_enable"] = True
        with TemporaryDirectory() as directory:
            path = Path(directory) / "drone_plugin.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            checks, skipped = _runtime_negative_checks(data,
                PluginRegistry.discover(manifest_paths=(path,)))
            self.assertEqual(checks, {"artifact_path_rejection": True})
            self.assertEqual(skipped, ["action_schema_rejection"])

    def test_undeclared_sensor_is_rejected_before_factory_import(self):
        data = json.loads((SAMPLE / "sample.json").read_text(encoding="utf-8"))
        data["required_sensor_resources"] = {"missing-camera": "sensor/rgb"}
        sys.modules.pop("drone_ext_sample", None)
        with self.assertRaises(PluginRegistryError) as caught:
            _preflight(self.registry(), data)
        self.assertEqual(caught.exception.issues[0].code, "insufficient_capability")
        self.assertEqual(caught.exception.issues[0].details["field"], "sensor_resources")
        self.assertNotIn("drone_ext_sample", sys.modules)

    def test_task_and_agent_requirements_reject_backend_before_import(self):
        data = json.loads((SAMPLE / "sample.json").read_text(encoding="utf-8"))
        data.pop("required_action_kinds")
        manifest = json.loads((SAMPLE / "src" / "drone_ext_sample" /
                               "drone_plugin.json").read_text(encoding="utf-8"))
        manifest["components"][3]["requires"] = {
            "sensor_types": ["sample.sensor/rgb", "sample.state/pose"],
            "sensor_resources": {"front": "sample.sensor/rgb"},
        }
        manifest["components"][5]["requires"] = {"action_kinds": ["sample/move"]}
        with TemporaryDirectory() as directory:
            path = Path(directory) / "drone_plugin.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            registry = PluginRegistry.discover(manifest_paths=(path,))
            self.assertEqual(registry.issues, ())
            sys.modules.pop("drone_ext_sample", None)
            with self.assertRaises(PluginRegistryError) as caught:
                _preflight(registry, data)
            self.assertEqual({item.details.get("field") for item in caught.exception.issues},
                             {"sensor_types", "sensor_resources"})
            self.assertNotIn("drone_ext_sample", sys.modules)
            manifest["components"][3]["requires"] = {}
            manifest["components"][0]["capabilities"]["action_kinds"] = []
            path.write_text(json.dumps(manifest), encoding="utf-8")
            registry = PluginRegistry.discover(manifest_paths=(path,))
            with self.assertRaises(PluginRegistryError) as caught:
                _preflight(registry, data)
            self.assertEqual(caught.exception.issues[0].code, "unsupported_action")

    def test_declared_requirements_merge_and_runtime_recheck_before_reset(self):
        data = json.loads((SAMPLE / "sample.json").read_text(encoding="utf-8"))
        data.pop("required_action_kinds")
        manifest = json.loads((SAMPLE / "src" / "drone_ext_sample" /
                               "drone_plugin.json").read_text(encoding="utf-8"))
        manifest["components"][0]["capabilities"].update({
            "sensor_types": ["sample.sensor/rgb", "sample.state/pose"],
            "sensor_resources": {"front": "sample.sensor/rgb"},
            "coordinate_frame": "local_ned",
            "time_bases": ["wall"],
        })
        manifest["components"][2]["requires"] = {"time_bases": ["wall"]}
        manifest["components"][3]["requires"] = {
            "sensor_types": ["sample.sensor/rgb", "sample.state/pose"],
            "sensor_resources": {"front": "sample.sensor/rgb"},
            "coordinate_frame": "local_ned",
        }
        manifest["components"][5]["requires"] = {"action_kinds": ["sample/move"]}
        with TemporaryDirectory() as directory:
            path = Path(directory) / "drone_plugin.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            registry = PluginRegistry.discover(manifest_paths=(path,))
            self.assertTrue(_preflight(registry, data))
            import drone_ext_sample
            from contracts.data_v02 import CapabilitySetV02
            with patch.object(drone_ext_sample.TwoVehicleMock, "capabilities",
                              lambda self: CapabilitySetV02(
                    ("sample/move",), ("sample.sensor/rgb", "sample.state/pose"),
                    {"front": "sample.sensor/rgb"}, "local_ned", ("wall",),
                    2, ("load", "reset"), True, True)):
                self.assertTrue(run_multi(data, Path(directory), registry=registry)["success"])
            def reset_must_not_run(*args):
                self.fail("reset called after capability mismatch")
            with patch.object(drone_ext_sample.TwoVehicleMock, "reset", reset_must_not_run):
                with self.assertRaises(PluginRegistryError) as caught:
                    run_multi(data | {"episode_id": "runtime-mismatch"},
                              Path(directory), registry=registry)
                self.assertEqual(caught.exception.issues[0].code, "insufficient_capability")

    def test_config_cannot_override_plugin_sensor_requirement(self):
        data = json.loads((SAMPLE / "sample.json").read_text(encoding="utf-8"))
        data["required_sensor_resources"] = {"front": "sample.sensor/depth"}
        manifest = json.loads((SAMPLE / "src" / "drone_ext_sample" /
                               "drone_plugin.json").read_text(encoding="utf-8"))
        manifest["components"][3]["requires"] = {
            "sensor_resources": {"front": "sample.sensor/rgb"}}
        with TemporaryDirectory() as directory:
            path = Path(directory) / "drone_plugin.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            registry = PluginRegistry.discover(manifest_paths=(path,))
            with self.assertRaises(PluginRegistryError) as caught:
                _preflight(registry, data)
            self.assertEqual(caught.exception.issues[0].code,
                             "conflicting_capability_requirement")
            self.assertEqual(caught.exception.issues[0].details["resource_id"], "front")

    def test_declared_sensor_is_rechecked_after_backend_discovery(self):
        data = json.loads((SAMPLE / "sample.json").read_text(encoding="utf-8"))
        data["required_sensor_resources"] = {"missing-camera": "sensor/rgb"}
        manifest = json.loads((SAMPLE / "src" / "drone_ext_sample" /
                               "drone_plugin.json").read_text(encoding="utf-8"))
        manifest["components"][0]["capabilities"]["sensor_resources"] = {
            "missing-camera": "sensor/rgb"}
        with TemporaryDirectory() as directory:
            path = Path(directory) / "drone_plugin.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            registry = PluginRegistry.discover(manifest_paths=(path,))
            with self.assertRaisesRegex(ValueError, "sensor resource"):
                run_multi(data, Path(directory), registry=registry)
            episode = Path(directory) / data["episode_id"]
            self.assertTrue((episode / "INCOMPLETE").is_file())
            self.assertFalse((episode / "result.json").exists())
            self.assertIn("sensor resource", (episode / "events.jsonl").read_text(encoding="utf-8"))

    def test_hard_cancellation_requires_manifest_declaration(self):
        data = json.loads((SAMPLE / "sample.json").read_text(encoding="utf-8"))
        data["require_hard_cancel"] = True
        sys.modules.pop("drone_ext_sample", None)
        with self.assertRaises(PluginRegistryError) as caught:
            _preflight(self.registry(), data)
        self.assertEqual(caught.exception.issues[0].code, "hard_cancel_unverified")
        self.assertNotIn("drone_ext_sample", sys.modules)
        manifest = json.loads((SAMPLE / "src" / "drone_ext_sample" /
                               "drone_plugin.json").read_text(encoding="utf-8"))
        manifest["components"][0]["capabilities"]["interruptible"] = True
        with TemporaryDirectory() as directory:
            path = Path(directory) / "drone_plugin.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            registry = PluginRegistry.discover(manifest_paths=(path,))
            with self.assertRaises(PluginRegistryError) as caught:
                _preflight(registry, data)
            self.assertEqual(caught.exception.issues[0].code, "hard_cancel_unverified")

    def test_legacy_adapter_records_plugin_identity_without_connection_secret(self):
        data = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        spec = TaskSpec.from_dict(data["task_spec"])
        config = BackendConfig("mock", "Drone", {"password": "sample-only-secret"})
        with TemporaryDirectory() as directory:
            runner = build_runner(spec, config, "fixed", Recorder(directory))
            result = runner.run(spec, config)
            episode = Path(directory) / result.episode_id
            backend = json.loads((episode / "backend.json").read_text(encoding="utf-8"))
            metadata = json.loads((episode / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(backend["connection"], {})
            self.assertEqual(metadata["plugins"]["backend"]["id"], "drone.mock/backend")


if __name__ == "__main__":
    unittest.main()
