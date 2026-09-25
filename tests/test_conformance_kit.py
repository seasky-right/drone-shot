"""Executable positive and negative cases for every candidate plugin type."""

from __future__ import annotations

import json
import contextlib
import io
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from contracts.plugin_v02 import PluginType
from contracts.data_v02 import CapabilitySetV02, ScenarioSpecV02
from core.conformance import declared_type_coverage, validate_component_cases, validate_plugin
from core.plugins import PluginRegistry, PluginRegistryError
from core.plugin_cli import main as plugin_main


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "external_plugin"
MANIFEST = SAMPLE / "src" / "drone_ext_sample" / "drone_plugin.json"


class ConformanceKitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(SAMPLE / "src"))

    @classmethod
    def tearDownClass(cls) -> None:
        sys.path.remove(str(SAMPLE / "src"))
        sys.modules.pop("drone_ext_sample", None)

    def cases(self) -> dict[str, object]:
        return json.loads((SAMPLE / "component-cases.json").read_text(encoding="utf-8"))

    def test_every_type_runs_its_actual_hook_and_reports_checks(self) -> None:
        registry = PluginRegistry.discover(manifest_paths=(MANIFEST,))
        coverage = declared_type_coverage(registry)
        self.assertEqual(set(coverage), {kind.value for kind in PluginType})
        self.assertTrue(all(coverage.values()))
        cases = self.cases()
        self.assertEqual(set(cases), {item.id for item in registry.list()})
        checked = validate_component_cases(registry, cases)
        self.assertTrue(all(item["status"] == "passed" for item in checked.values()),
                        checked)
        for item in checked.values():
            self.assertIn("close", item["checks"])
            self.assertGreater(len(item["checks"]), 2)
        report = validate_plugin(registry, component_cases=cases)
        self.assertTrue(report["valid"])
        self.assertTrue(report["complete"])
        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["runtime_checked"])
        self.assertEqual(report["unchecked_components"], [])

    def test_sample_runs_and_reports_negative_checks(self) -> None:
        registry = PluginRegistry.discover(manifest_paths=(MANIFEST,))
        sample = json.loads((SAMPLE / "sample.json").read_text(encoding="utf-8"))
        with TemporaryDirectory() as temporary:
            report = validate_plugin(registry, sample=sample, output=temporary)
            self.assertTrue(report["valid"])
            self.assertEqual(report["sample"]["step_count"], 1)
            self.assertEqual(report["checks"], {
                "artifact_path_rejection": True,
                "action_schema_rejection": True,
                "failure_incomplete_record": True,
                "result_reread": True,
                "truth_field_rejection": True,
            })
            self.assertEqual(report["declared_types"]["backend"], ["sample.mock/backend"])
            self.assertEqual(report["component_results"]["sample.mock/backend"]["status"],
                             "unchecked")
            self.assertFalse(report["complete"])
            self.assertEqual(report["status"], "partial")

    def test_invalid_manifest_dependency_and_case_config_are_rejected(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        manifest["dependencies"] = [{"id": "missing.pack", "version": ">=1"}]
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "drone_plugin.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            registry = PluginRegistry.discover(manifest_paths=(path,))
            with self.assertRaises(PluginRegistryError):
                validate_plugin(registry)
        registry = PluginRegistry.discover(manifest_paths=(MANIFEST,))
        result = validate_component_cases(
            registry, {"sample.central/agent": {"config": {"token": []}}})
        self.assertEqual(result["sample.central/agent"]["status"], "failed")
        self.assertEqual(result["sample.central/agent"]["stage"], "config")

    def test_each_type_rejects_invalid_runtime_hook_and_still_closes(self) -> None:
        import drone_ext_sample as sample

        class BadComponent:
            def __init__(self, kind):
                self.kind = kind
                self.closed = False
                self.spec = ScenarioSpecV02(
                    "sample.urban", "1.0.0", "bad-truth", {}, 7, {},
                    truth_access="none")
                self.truth = {"secret": True}

            def capabilities(self):
                return CapabilitySetV02(
                    ("sample/move",), (), {}, "local_ned", ("wall",), 1,
                    ("load", "reset"), True, True)

            def reset(self, scenario, vehicle_ids):
                raise AssertionError("capacity mismatch should fail before reset")

            def execute(self, action):
                raise AssertionError("capacity mismatch should fail before execute")

            def observe(self):
                raise AssertionError("capacity mismatch should fail before observe")

            def complete(self, snapshot, truth):
                return "not a boolean"

            def act(self, snapshot):
                return ()

            def evaluate(self, result):
                return {"bad": float("nan")}

            def generate(self, seed):
                return object()

            def acquire(self, episode_id, request):
                return episode_id

            def run(self, session_factory):
                return ()

            def process(self, read_result, episode_ids):
                return []

            def cases(self):
                return ({"case_id": "same"}, {"case_id": "same"})

            def close(self):
                self.closed = True

        factories = {
            "backend": ("sample.mock/backend", "create_backend", "capabilities"),
            "task": ("sample.search/task", "create_task", "complete"),
            "agent": ("sample.central/agent", "create_agent", "act"),
            "evaluator": ("sample.search/evaluator", "create_evaluator", "evaluate"),
            "scenario": ("sample.urban/scenario", "create_scenario", "truth"),
            "scenario_generator": ("sample.urban/generator", "create_scenario_generator", "generate"),
            "runtime_provider": ("sample.mock/runtime", "create_runtime", "hooks"),
            "training_driver": ("sample.training/driver", "create_training_driver", "run"),
            "result_processor": ("sample.success/processor", "create_result_processor", "process"),
            "benchmark": ("sample.search/benchmark", "create_benchmark", "cases"),
        }
        registry = PluginRegistry.discover(manifest_paths=(MANIFEST,))
        for kind, (identifier, factory_name, stage) in factories.items():
            with self.subTest(kind=kind):
                bad = BadComponent(kind)
                with patch.object(sample, factory_name, return_value=bad):
                    result = validate_component_cases(
                        registry, {identifier: self.cases()[identifier]})[identifier]
                self.assertEqual(result["status"], "failed", result)
                self.assertEqual(result["stage"], stage, result)
                self.assertTrue(bad.closed)

    def test_declared_resource_mismatch_and_cleanup_failure_are_reported(self) -> None:
        import drone_ext_sample as sample

        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        manifest["components"][0]["capabilities"]["sensor_resources"] = {
            "front": "sample.sensor/rgb"}
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "drone_plugin.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            registry = PluginRegistry.discover(manifest_paths=(path,))
            item = validate_component_cases(
                registry, {"sample.mock/backend": {"config": {}}})["sample.mock/backend"]
            self.assertEqual(item["status"], "failed")
            self.assertEqual(item["stage"], "capabilities")
            self.assertIn("front", item["reason"])

        class BadClose(sample.SearchTask):
            def close(self):
                raise RuntimeError("cleanup failed")

        registry = PluginRegistry.discover(manifest_paths=(MANIFEST,))
        with patch.object(sample, "create_task", return_value=BadClose()):
            item = validate_component_cases(
                registry, {"sample.search/task": {"config": {}}})["sample.search/task"]
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["stage"], "close")
        self.assertEqual(item["cleanup_error"], "RuntimeError")

        class NoClose:
            def complete(self, snapshot, truth):
                return False

        with patch.object(sample, "create_task", return_value=NoClose()):
            item = validate_component_cases(
                registry, {"sample.search/task": {"config": {}}})["sample.search/task"]
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["stage"], "hooks")
        self.assertIn("close", item["reason"])

    def test_generator_partial_failure_closes_prior_output(self) -> None:
        import drone_ext_sample as sample

        class Generated:
            def __init__(self):
                self.closed = False
                self.spec = ScenarioSpecV02(
                    "sample.urban", "1.0.0", "partial", {}, 7, {})

            def close(self):
                self.closed = True

        class FailingGenerator:
            def __init__(self):
                self.output = Generated()
                self.calls = 0
                self.closed = False

            def generate(self, seed):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("second generation failed")
                return self.output

            def close(self):
                self.closed = True

        generator = FailingGenerator()
        registry = PluginRegistry.discover(manifest_paths=(MANIFEST,))
        with patch.object(sample, "create_scenario_generator",
                          return_value=generator):
            item = validate_component_cases(
                registry, {"sample.urban/generator": {"config": {}}}
            )["sample.urban/generator"]
        self.assertEqual(item["status"], "failed")
        self.assertTrue(generator.output.closed)
        self.assertTrue(generator.closed)

    def test_explicit_enable_reports_unsupported_without_factory_import(self) -> None:
        import drone_ext_sample as sample

        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        manifest["components"][0]["capabilities"]["requires_explicit_enable"] = True
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "drone_plugin.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            registry = PluginRegistry.discover(manifest_paths=(path,))
            with patch.object(sample, "create_backend") as factory:
                item = validate_component_cases(
                    registry, {"sample.mock/backend": {"config": {}}})["sample.mock/backend"]
                factory.assert_not_called()
            self.assertEqual(item["status"], "unsupported")
            self.assertEqual(item["stage"], "preflight")
            cases_path = Path(temporary) / "cases.json"
            cases_path.write_text(json.dumps({
                "sample.mock/backend": {"config": {}},
            }), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = plugin_main([
                    "validate-plugin", "--manifest", str(path),
                    "--component-cases", str(cases_path),
                ])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())
                             ["component_results"]["sample.mock/backend"]["status"],
                             "unsupported")

    def test_legacy_component_is_unsupported_without_import(self) -> None:
        import drone_ext_sample as sample

        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        manifest["components"][0]["capabilities"]["component_api"] = (
            "drone.plugin.api/v0.1")
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "drone_plugin.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            registry = PluginRegistry.discover(manifest_paths=(path,))
            unselected = validate_plugin(registry)
            item = unselected["component_results"]["sample.mock/backend"]
            self.assertEqual(item["status"], "unsupported")
            self.assertEqual(item["stage"], "contract")
            with patch.object(sample, "create_backend") as factory:
                selected = validate_plugin(
                    registry, component_cases={"sample.mock/backend": {"config": {}}})
                factory.assert_not_called()
            self.assertFalse(selected["valid"])
            self.assertFalse(selected["complete"])
            self.assertEqual(selected["component_results"]["sample.mock/backend"], item)

    def test_cli_reports_component_verdicts_and_fails_bad_case(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            good = io.StringIO()
            with contextlib.redirect_stdout(good):
                code = plugin_main([
                    "validate-plugin", "--manifest", str(MANIFEST),
                    "--component-cases", str(SAMPLE / "component-cases.json"),
                    "--output", str(root / "runs"),
                ])
            self.assertEqual(code, 0)
            report = json.loads(good.getvalue())
            self.assertTrue(report["complete"])
            self.assertEqual(report["status"], "passed")
            self.assertEqual(len(report["checked_components"]), 11)

            bad_cases = root / "bad-cases.json"
            bad_cases.write_text(json.dumps({
                "sample.central/agent": {"config": {"token": []}},
            }), encoding="utf-8")
            bad = io.StringIO()
            with contextlib.redirect_stdout(bad):
                code = plugin_main([
                    "validate-plugin", "--manifest", str(MANIFEST),
                    "--component-cases", str(bad_cases),
                    "--output", str(root / "runs"),
                ])
            self.assertEqual(code, 1)
            report = json.loads(bad.getvalue())
            item = report["component_results"]["sample.central/agent"]
            self.assertEqual(item["status"], "failed")
            self.assertEqual(item["stage"], "config")
            self.assertEqual(report["status"], "failed")


if __name__ == "__main__":
    unittest.main()
