"""Batch the same ReachPoint TaskSpec across Track C agents through Track A."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from core.experiment_cli import main
from core.replay import read_episode


CONFIG = Path(__file__).resolve().parents[1] / "reachpoint.experiment.example.json"


class ReachPointBatchIntegrationTests(unittest.TestCase):
    def test_all_agents_share_seed_repeat_matrix_and_recorded_task(self) -> None:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        config["seeds"] = [3, 7]
        config["repeats"] = 2
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "plan.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            printed = StringIO()
            with redirect_stdout(printed):
                code = main(["--config", str(config_path), "--output", str(root / "experiments")])
            self.assertEqual(code, 0)
            output = json.loads(printed.getvalue())
            self.assertEqual(output["episode_count"], 12)
            experiment = Path(output["summary_path"]).parent
            summary = json.loads(Path(output["summary_path"]).read_text(encoding="utf-8"))
            self.assertEqual(summary["episode_count"], 12)
            self.assertEqual(summary["planned_episode_count"], 12)
            expected_steps = {"direct": 2, "fixed-route": 4, "legacy-route": 3}
            path_lengths = {}
            for agent_index, (name, count) in enumerate(expected_steps.items()):
                group = summary["agents"][name]
                self.assertEqual(group["task_success_count"], 4)
                self.assertEqual(group["infrastructure_error_count"], 0)
                self.assertEqual(group["cleanup_failure_count"], 0)
                self.assertEqual(group["metrics"]["final_distance_m"]["mean"], 0.0)
                path_lengths[name] = group["metrics"]["path_length_m"]["mean"]
                for offset, seed in enumerate((3, 3, 7, 7), start=1):
                    episode = experiment / "episodes" / f"episode-{agent_index * 4 + offset:06d}"
                    replay = read_episode(episode)
                    self.assertEqual(replay.task.seed, seed)
                    self.assertEqual(len(replay.steps), count)
                    self.assertTrue(replay.result.success)
                    self.assertTrue(replay.result.cleanup.succeeded)
            self.assertLess(path_lengths["direct"], path_lengths["fixed-route"])
            self.assertLess(path_lengths["fixed-route"], path_lengths["legacy-route"])
            self.assertFalse((experiment / "INCOMPLETE").exists())


if __name__ == "__main__":
    unittest.main()
