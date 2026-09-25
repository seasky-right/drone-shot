"""Terminal view over existing episode execution and recordings."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from core.console import main
from core.replay import read_episode


ROOT = Path(__file__).resolve().parents[1]


class ConsoleTests(unittest.TestCase):
    def test_run_status_and_show_use_one_recorded_mock_episode(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "runs" / "mock-check"
            printed = StringIO()
            with redirect_stdout(printed):
                exit_code = main(["run", "--config", str(ROOT / "config.example.json"),
                                  "--output", str(output)])
            self.assertEqual(exit_code, 0)
            self.assertIn("[运行中]", printed.getvalue())
            self.assertIn("[执行中] move_to", printed.getvalue())
            self.assertIn("final_distance_m=0.000", printed.getvalue())
            episode = next(output.iterdir())
            self.assertTrue(read_episode(episode).result.success)

            with redirect_stdout(printed := StringIO()):
                self.assertEqual(main(["status", "--output", str(output.parent)]), 0)
            self.assertIn("reach-point-example", printed.getvalue())
            with redirect_stdout(printed := StringIO()):
                self.assertEqual(main(["show", "--output", str(output.parent)]), 0)
            self.assertIn("动作/事件  1 / 5", printed.getvalue())

    def test_status_marks_incomplete_record_without_claiming_it_is_running(self) -> None:
        with TemporaryDirectory() as directory:
            episode = Path(directory) / "unfinished"
            episode.mkdir()
            (episode / "INCOMPLETE").write_text("unfinished", encoding="utf-8")
            with redirect_stdout(printed := StringIO()):
                self.assertEqual(main(["status", "--output", directory]), 0)
            self.assertIn("进行中或未完成", printed.getvalue())

    def test_airsim_requires_explicit_gate_before_backend_creation(self) -> None:
        with TemporaryDirectory() as directory:
            data = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            data["backend_config"]["backend_type"] = "airsim"
            config = Path(directory) / "config.json"
            config.write_text(json.dumps(data), encoding="utf-8")
            with patch("backends.airsim.AirSimBackend") as backend, redirect_stderr(stderr := StringIO()):
                with self.assertRaises(SystemExit) as result:
                    main(["run", "--config", str(config), "--output", directory])
            self.assertEqual(result.exception.code, 2)
            self.assertIn("--enable-airsim", stderr.getvalue())
            backend.assert_not_called()


if __name__ == "__main__":
    unittest.main()
