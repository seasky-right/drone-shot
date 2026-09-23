"""Offline FR-EVL-02/03/04 acceptance and fairness checks."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from contracts import ContractValidationError
from evaluators.benchmark import summarize_results
from evaluators.record import RecordedEpisode, evaluate_record
from scripts.generate_reach_point_demo import generate

ROOT = Path(__file__).resolve().parents[1]


class EvaluationPipelineTests(unittest.TestCase):
    def test_cli_generates_episode_results_and_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generate(root / "records")
            subprocess.run([sys.executable, "-B", "-m", "evaluators.cli", "evaluate-batch",
                            str(root / "records"), "--output-dir", str(root / "results")],
                           cwd=ROOT, check=True, capture_output=True, text=True)
            paths = sorted((root / "results").rglob("result.json"))
            self.assertEqual(len(paths), 4)
            results = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
            fast = next(r for r in results if r["agent_id"] == "synthetic_fast" and r["repeat_index"] == 0)
            self.assertEqual(fast["metrics"]["completion_time_s"], 4)
            self.assertEqual(fast["scores"], {"goal": 70, "time": 18, "total": 88})
            self.assertEqual(fast["trajectory_summary"]["path_length_m"], 10)
            self.assertTrue(Path(fast["record_path"]).exists())
            subprocess.run([sys.executable, "-B", "-m", "evaluators.cli", "summarize",
                            str(root / "results"), "--output", str(root / "summary.json"),
                            "--report", str(root / "report.md")],
                           cwd=ROOT, check=True, capture_output=True, text=True)
            summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["ranking"], ["synthetic_fast", "synthetic_slow"])
            self.assertEqual(summary["agents"]["synthetic_fast"]["score_mean"], 86.5)
            self.assertEqual(summary["agents"]["synthetic_slow"]["score_mean"], 74.5)
            self.assertIn("synthetic_fast", (root / "report.md").read_text(encoding="utf-8"))

    def test_infrastructure_error_is_visible_and_withholds_ranking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            records = generate(Path(directory))
            results = [evaluate_record(RecordedEpisode.from_dict(json.loads(p.read_text(encoding="utf-8"))),
                                       str(p)) for p in records]
            changed = copy.deepcopy(results)
            changed[0]["termination_reason"] = "backend_error"
            changed[0]["success"] = False
            changed[0]["scores"] = {"goal": 0, "time": 0, "total": 0}
            summary = summarize_results(changed)
            fast = summary["agents"]["synthetic_fast"]
            self.assertEqual(fast["attempts"], 2)
            self.assertEqual(fast["evaluated_episodes"], 1)
            self.assertEqual(fast["infrastructure_errors"], 1)
            self.assertEqual(fast["error_rates_by_attempt"]["backend_error"], 0.5)
            self.assertEqual(summary["ranking"], [])

    def test_mismatched_conditions_and_missing_repeats_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            records = generate(Path(directory))
            results = [evaluate_record(RecordedEpisode.from_dict(json.loads(p.read_text(encoding="utf-8"))),
                                       str(p)) for p in records]
            with self.assertRaises(ContractValidationError):
                summarize_results(results[:-1])
            changed = copy.deepcopy(results)
            changed[-1]["task_spec"]["parameters"]["tolerance_m"] = 5
            with self.assertRaises(ContractValidationError):
                summarize_results(changed)

    def test_invalid_elapsed_or_claimed_success_cannot_be_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = generate(Path(directory))[0]
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["steps"][0]["elapsed_monotonic_s"] = -1
            with self.assertRaises(ContractValidationError):
                evaluate_record(RecordedEpisode.from_dict(raw), str(path))
            raw["steps"][0]["elapsed_monotonic_s"] = 4
            raw["steps"][0]["observation_after"]["position_ned"]["north_m"] = 8
            with self.assertRaises(ContractValidationError):
                evaluate_record(RecordedEpisode.from_dict(raw), str(path))


if __name__ == "__main__":
    unittest.main()
