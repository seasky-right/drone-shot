"""The v0.2 generator, evaluator, and benchmark run through public hooks."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import sys
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

import pytest

from core.plugin_cli import main, run_benchmark, run_multi
from core.plugins import PluginRegistry
from core.store import EpisodeStore


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "builtin_pack"


def _registry() -> PluginRegistry:
    registry = PluginRegistry.discover(manifest_paths=(PACK / "drone_plugin.json",))
    assert not registry.issues
    return registry


def _sample() -> dict:
    return json.loads((PACK / "sample-benchmark-v02.json").read_text(encoding="utf-8"))


def test_benchmark_generates_scores_and_rereads_two_cases(tmp_path):
    summary = run_benchmark(_sample(), tmp_path, registry=_registry())

    assert summary["case_count"] == 2
    assert summary["success_count"] == 2
    assert summary["processed"] == {"count": 2, "success_count": 2}
    assert [case["seed"] for case in summary["cases"]] == [7, 11]
    assert all(case["metrics"] == {"drone/success": 1.0}
               for case in summary["cases"])
    assert EpisodeStore.read_result(tmp_path / "benchmarks", summary["episode_id"]) == summary

    results = [EpisodeStore.read_result(tmp_path, case["episode_id"])
               for case in summary["cases"]]
    assert results[0]["scenario"]["checksum"] != results[1]["scenario"]["checksum"]
    for result in results:
        assert result["metrics"] == {"drone/success": 1.0}
        episode = tmp_path / result["episode_id"]
        assert "target_north_m" not in (episode / "result.json").read_text(encoding="utf-8")
        assert "target_north_m" not in (episode / "trajectory.jsonl").read_text(encoding="utf-8")


def test_benchmark_cli_uses_selected_pack_without_a_simulator(tmp_path):
    printed = StringIO()
    with redirect_stdout(printed):
        exit_code = main(["run-benchmark", str(PACK / "sample-benchmark-v02.json"),
                          "--manifest", str(PACK / "drone_plugin.json"),
                          "--output", str(tmp_path)])
    assert exit_code == 0
    assert json.loads(printed.getvalue())["case_count"] == 2


def test_generator_requires_explicit_matching_seed(tmp_path):
    sample = _sample()
    sample.pop("benchmark")
    sample.pop("processor")
    sample["episode_id"] = "missing-seed"
    with pytest.raises(ValueError, match="nonnegative integer seed"):
        run_multi(sample, tmp_path, registry=_registry())
    assert (tmp_path / "missing-seed" / "INCOMPLETE").is_file()


def test_external_scenario_and_evaluator_join_the_same_pipeline(tmp_path, monkeypatch):
    external = ROOT / "examples" / "external_plugin"
    monkeypatch.syspath_prepend(str(external / "src"))
    registry = PluginRegistry.discover(manifest_paths=(
        external / "src" / "drone_ext_sample" / "drone_plugin.json",))
    assert not registry.issues
    data = json.loads((external / "sample.json").read_text(encoding="utf-8"))
    data["components"]["evaluator"] = {"id": "sample.search/evaluator"}
    result = run_multi(data, tmp_path, registry=registry)
    assert result["metrics"] == {"sample/success": 1.0}
    assert EpisodeStore.read_result(tmp_path, result["episode_id"]) == result
    sys.modules.pop("drone_ext_sample", None)


def test_invalid_evaluator_does_not_publish_a_scored_result(tmp_path):
    import builtin_pack.v02 as builtin_v02

    class InvalidEvaluator:
        def evaluate(self, result):
            return {"drone/success": float("nan")}

        def close(self):
            pass

    sample = _sample()
    sample.pop("benchmark")
    sample.pop("processor")
    sample["seed"] = 7
    sample["episode_id"] = "bad-evaluation"
    with patch.object(builtin_v02, "create_evaluator", return_value=InvalidEvaluator()):
        with pytest.raises(ValueError, match="finite numeric metrics"):
            run_multi(sample, tmp_path, registry=_registry())
    assert (tmp_path / "bad-evaluation" / "INCOMPLETE").is_file()
    assert not (tmp_path / "bad-evaluation" / "result.json").exists()


def test_evaluator_reads_artifacts_and_hidden_truth_without_publishing_them(tmp_path):
    import builtin_pack.v02 as builtin_v02

    class InspectEvaluator:
        def evaluate(self, result):
            assert result["truth"] == {"target_north_m": 4}
            assert (Path(result["artifact_root"]) / "metadata" / "scenario.json").is_file()
            assert result["trajectory"]
            return {"drone/inspection": 1.0}

        def close(self):
            pass

    sample = _sample()
    sample.pop("benchmark")
    sample.pop("processor")
    sample["seed"] = 7
    sample["episode_id"] = "evaluator-context"
    with patch.object(builtin_v02, "create_evaluator", return_value=InspectEvaluator()):
        result = run_multi(sample, tmp_path, registry=_registry())
    assert result["metrics"] == {"drone/inspection": 1.0}
    assert "truth" not in result and "artifact_root" not in result


def test_generator_cannot_change_its_declared_scenario_identity(tmp_path):
    import builtin_pack.v02 as builtin_v02

    class WrongScenarioGenerator(builtin_v02.ScenarioGenerator):
        def generate(self, seed):
            item = super().generate(seed)
            item.spec = replace(item.spec, scenario_id="unrelated.scene")
            return item

    sample = _sample()
    sample.pop("benchmark")
    sample.pop("processor")
    sample["seed"] = 7
    sample["episode_id"] = "wrong-scenario"
    with patch.object(builtin_v02, "create_generator", return_value=WrongScenarioGenerator()):
        with pytest.raises(ValueError, match="scenario ID differs"):
            run_multi(sample, tmp_path, registry=_registry())
    assert (tmp_path / "wrong-scenario" / "INCOMPLETE").is_file()


def test_benchmark_rejects_missing_case_seed_before_running_cases(tmp_path):
    import builtin_pack.v02 as builtin_v02

    class MissingSeedBenchmark:
        def cases(self):
            return ({"case_id": "first", "seed": 7}, {"case_id": "second"})

        def close(self):
            pass

    sample = _sample()
    with patch.object(builtin_v02, "create_benchmark", return_value=MissingSeedBenchmark()):
        with pytest.raises(ValueError, match="case seed"):
            run_benchmark(sample, tmp_path, registry=_registry())
    assert (tmp_path / "benchmarks" / sample["benchmark_id"] / "INCOMPLETE").is_file()
    assert not (tmp_path / f"{sample['benchmark_id']}-0").exists()


def test_fixed_scenario_case_clears_inherited_generator_seed(tmp_path):
    import builtin_pack.v02 as builtin_v02

    class FixedScenarioBenchmark:
        def cases(self):
            return ({"case_id": "fixed", "components": {
                "scenario": {"id": "drone.v02.mock/scenario"}}},)

        def close(self):
            pass

    sample = _sample()
    sample["seed"] = 11
    with patch.object(builtin_v02, "create_benchmark", return_value=FixedScenarioBenchmark()):
        with patch("core.plugin_cli.run_multi", wraps=run_multi) as running:
            result = run_benchmark(sample, tmp_path, registry=_registry())
    assert "seed" not in running.call_args.args[0]
    assert result["case_count"] == 1


def test_benchmark_records_case_error_without_publishing_summary(tmp_path):
    import builtin_pack.v02 as builtin_v02

    class BrokenEvaluator:
        def evaluate(self, result):
            raise RuntimeError("scoring failed")

        def close(self):
            pass

    sample = _sample()
    with patch.object(builtin_v02, "create_evaluator", return_value=BrokenEvaluator()):
        with pytest.raises(RuntimeError, match="scoring failed"):
            run_benchmark(sample, tmp_path, registry=_registry())

    benchmark_root = tmp_path / "benchmarks" / sample["benchmark_id"]
    assert (benchmark_root / "INCOMPLETE").is_file()
    assert not (benchmark_root / "result.json").exists()
    events = [json.loads(line) for line in (benchmark_root / "events.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert events[-1]["name"] == "component_error"
    assert events[-1]["payload"]["stage"] == "benchmark.run_case"
    assert events[-1]["payload"]["case_id"] == "drone.mock.seed-7"
    assert "scoring failed" in events[-1]["payload"]["error"]
    assert (tmp_path / "builtin-v02-benchmark-0" / "INCOMPLETE").is_file()
