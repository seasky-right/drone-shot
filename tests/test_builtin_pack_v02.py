"""Executable coverage for the first-party v0.2 components and v0.1 boundary."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from contracts.data_v02 import ActionChannel, ActionV02
from core.conformance import validate_component_cases, validate_plugin
from core.plugin_cli import run_multi
from core.plugins import PluginRegistry
from core.store import EpisodeStore


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "builtin_pack"
MANIFEST = PACK / "drone_plugin.json"
CASES = PACK / "component-cases.json"
SAMPLE = PACK / "sample-v02.json"


def _registry(path: Path = MANIFEST) -> PluginRegistry:
    registry = PluginRegistry.discover(manifest_paths=(path,))
    assert not registry.issues
    return registry


def test_builtin_cases_execute_each_v02_type_and_mark_legacy_unsupported():
    registry = _registry()
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    assert len(cases) == 10
    report = validate_plugin(registry, component_cases=cases)
    results = report["component_results"]
    assert len(results) == 24
    assert {item["type"] for key, item in results.items() if key in cases} == {
        "backend", "task", "agent", "evaluator", "scenario",
        "scenario_generator", "runtime_provider", "training_driver",
        "result_processor", "benchmark",
    }
    for component_id in cases:
        assert results[component_id]["status"] == "passed", results[component_id]
        assert len(results[component_id]["checks"]) >= 3
    legacy = set(results) - set(cases)
    assert len(legacy) == 14
    assert all(results[key]["status"] == "unsupported" for key in legacy)
    assert all(registry.resolve(key).capabilities["component_api"]
               == "drone.plugin.api/v0.1" for key in legacy)
    assert report["complete"] is False


def test_v02_builtin_mock_runs_two_vehicles_and_rereads_result():
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    with TemporaryDirectory() as temporary:
        result = run_multi(sample, temporary, registry=_registry())
        reread = EpisodeStore.read_result(temporary, result["episode_id"])
    assert result["success"] is True
    assert reread == result
    assert set(result["final_snapshot"]["observations"]) == {"A", "B"}
    assert all("ground_truth" not in observation and "truth" not in observation
               for observation in result["final_snapshot"]["observations"].values())


def test_declared_backend_capacity_mismatch_fails_the_executable_probe(tmp_path):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    backend = next(component for component in manifest["components"]
                   if component["id"] == "drone.v02.mock/backend")
    backend["capabilities"]["max_vehicles"] = 3
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    result = validate_component_cases(_registry(path), {
        "drone.v02.mock/backend": cases["drone.v02.mock/backend"],
    })["drone.v02.mock/backend"]
    assert result["status"] == "failed"
    assert result["stage"] == "capabilities"
    assert "capacity" in result["reason"]


def test_agent_action_kind_mismatch_fails_after_act_and_closes():
    import builtin_pack.v02 as builtin_v02

    closed = []

    class WrongAgent:
        def act(self, snapshot):
            return (ActionV02("wrong", "A", "other/move", ActionChannel.CONTROL,
                              "drone.move/v1", {"north_m": 5}, 1),)

        def close(self):
            closed.append(True)

    with patch.object(builtin_v02, "create_agent", return_value=WrongAgent()):
        result = validate_component_cases(_registry(), {
            "drone.v02.mock/agent": {"config": {}},
        })["drone.v02.mock/agent"]
    assert result["status"] == "failed"
    assert result["stage"] == "capabilities"
    assert closed == [True]
