"""Cross-track Mock integration through the installed CLI implementation."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from core.cli import main


class P2CliIntegrationTests(unittest.TestCase):
    def test_reachpoint_cli_records_c_agent_and_evaluator(self) -> None:
        root = Path(__file__).resolve().parents[1]
        config = root / "config.example.json"
        with TemporaryDirectory() as directory:
            output = Path(directory) / "runs"
            printed = StringIO()
            with redirect_stdout(printed):
                exit_code = main(["--config", str(config), "--agent", "fixed", "--output", str(output)])
            self.assertEqual(exit_code, 0)
            result = json.loads(printed.getvalue())
            self.assertTrue(result["success"])
            self.assertEqual(result["termination_reason"], "success")
            episode = output / result["episode_id"]
            self.assertEqual(json.loads((episode / "result.json").read_text(encoding="utf-8")), result)
            self.assertTrue((episode / "task.json").is_file())
            self.assertTrue((episode / "backend.json").is_file())
            steps = [json.loads(line) for line in (episode / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(steps), 1)
            self.assertEqual(steps[0]["action"]["action_id"], "route-1")
            self.assertEqual(steps[0]["observation_after"]["position_ned"], {"north_m": 5.0, "east_m": 0.0, "down_m": -2.0})
            self.assertGreater(result["metrics"]["path_length_m"], 0.0)
            self.assertTrue((episode / "events.jsonl").read_text(encoding="utf-8").strip())


if __name__ == "__main__":
    unittest.main()
