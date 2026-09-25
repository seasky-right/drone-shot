"""CLI backend selection without a running simulator."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backends.mock import MockBackend
from contracts import CleanupResult, ContractError
from core.cli import main


ROOT = Path(__file__).resolve().parents[1]


class InjectedAirSimBackend(MockBackend):
    resets = 0

    def reset(self, config, task):
        type(self).resets += 1
        return super().reset(replace(config, backend_type="mock"), task)


class CleanupFailingAirSimBackend(InjectedAirSimBackend):
    def cleanup(self):
        return CleanupResult(True, False, ContractError("simulator", "landing failed"))


class CliBackendSelectionTests(unittest.TestCase):
    def _config(self, directory: str, backend_type: str) -> Path:
        data = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        data["backend_config"]["backend_type"] = backend_type
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_airsim_requires_explicit_switch_before_backend_construction(self):
        with TemporaryDirectory() as directory:
            config = self._config(directory, "airsim")
            stderr = StringIO()
            with patch("backends.airsim.AirSimBackend") as backend_type, redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as exit_result:
                    main(["--config", str(config), "--output", directory])
            self.assertEqual(exit_result.exception.code, 2)
            self.assertIn("--enable-airsim", stderr.getvalue())
            backend_type.assert_not_called()

    def test_airsim_switch_selects_backend_and_records_episode(self):
        with TemporaryDirectory() as directory:
            config = self._config(directory, "airsim")
            output = Path(directory) / "runs"
            printed = StringIO()
            InjectedAirSimBackend.resets = 0
            with patch("backends.airsim.AirSimBackend", InjectedAirSimBackend), redirect_stdout(printed):
                code = main(["--config", str(config), "--agent", "fixed",
                             "--output", str(output), "--enable-airsim"])
            result = json.loads(printed.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(result["success"])
            self.assertEqual(InjectedAirSimBackend.resets, 1)
            episode = output / result["episode_id"]
            recorded = json.loads((episode / "backend.json").read_text(encoding="utf-8"))
            self.assertEqual(recorded["backend_type"], "airsim")
            self.assertTrue((episode / "trajectory.jsonl").is_file())

    def test_cleanup_failure_returns_nonzero_without_rewriting_task_outcome(self):
        with TemporaryDirectory() as directory:
            config = self._config(directory, "airsim")
            output = Path(directory) / "runs"
            printed = StringIO()
            with patch("backends.airsim.AirSimBackend", CleanupFailingAirSimBackend), redirect_stdout(printed):
                code = main(["--config", str(config), "--output", str(output),
                             "--enable-airsim"])
            result = json.loads(printed.getvalue())
            self.assertEqual(code, 1)
            self.assertTrue(result["success"])
            self.assertFalse(result["cleanup"]["succeeded"])

    def test_unknown_backend_type_is_rejected(self):
        with TemporaryDirectory() as directory:
            config = self._config(directory, "unknown")
            stderr = StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_result:
                main(["--config", str(config), "--output", directory])
            self.assertEqual(exit_result.exception.code, 2)
            self.assertIn("unsupported backend_type", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
