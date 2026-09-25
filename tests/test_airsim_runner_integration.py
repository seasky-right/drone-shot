from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backends.airsim import AirSimBackend
from contracts import BackendConfig, PositionNed, TaskSpec, TerminationReason
from core.cli import main
from core.replay import read_episode
from simulator_contract.legacy_airsim import AirSimLegacyAdapter
from tests.test_airsim_backend import FakeRpc


class AirSimRunnerIntegrationTests(unittest.TestCase):
    def test_airsim_cli_records_and_replays_full_reach_point_episode(self):
        rpc = FakeRpc()
        target = PositionNed(0, 1, -1)
        task = TaskSpec(
            "airsim-runner", "reach_point", 15, PositionNed(0, 0, 0),
            "relative_to_home",
            {"target_position_ned": target.to_dict(), "tolerance_m": 0.25},
        )
        config = BackendConfig(
            "airsim", "drone-1",
            {"sensors": [{"sensor_id": "0", "kind": "rgb"}],
             "position_tolerance_m": 0.1},
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_file = root / "config.json"
            config_file.write_text(
                json.dumps({"task_spec": task.to_dict(),
                            "backend_config": config.to_dict()}),
                encoding="utf-8",
            )
            output = root / "runs"
            printed = StringIO()
            def fake_backend():
                return AirSimBackend(
                    lambda **options: AirSimLegacyAdapter(
                        rpc_factory=lambda host, port: rpc, **options
                    )
                )
            with patch("backends.airsim.AirSimBackend", side_effect=fake_backend):
                with redirect_stdout(printed):
                    exit_code = main([
                        "--config", str(config_file), "--agent", "fixed",
                        "--output", str(output), "--enable-airsim",
                    ])
            self.assertEqual(exit_code, 0)
            printed_result = json.loads(printed.getvalue())
            episode = output / printed_result["episode_id"]
            replay = read_episode(episode)
            result = replay.result
            self.assertEqual(result.termination_reason, TerminationReason.SUCCESS)
            self.assertTrue(result.cleanup.succeeded)
            self.assertEqual(len(replay.steps), 1)
            self.assertEqual(replay.backend.resource_root, str(episode.resolve()))
            before = replay.steps[0].observation_before.sensors[0]
            after = replay.steps[0].observation_after.sensors[0]
            self.assertNotEqual(before.relative_path, after.relative_path)
            self.assertEqual((episode / before.relative_path).read_bytes(), b"pngdata")
            self.assertEqual((episode / after.relative_path).read_bytes(), b"pngdata")
            self.assertFalse((episode / "INCOMPLETE").exists())
            self.assertEqual(
                json.loads((episode / "result.json").read_text(encoding="utf-8")),
                printed_result,
            )


    def test_failed_move_still_records_observation_and_landing(self):
        rpc = FakeRpc()
        rpc.fail_method = "moveByVelocityBodyFrame"
        target = PositionNed(0, 1, -1)
        task = TaskSpec(
            "airsim-failure", "reach_point", 15, PositionNed(0, 0, 0),
            "relative_to_home",
            {"target_position_ned": target.to_dict(), "tolerance_m": 0.25},
        )
        config = BackendConfig("airsim", "drone-1")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_file = root / "config.json"
            config_file.write_text(
                json.dumps({"task_spec": task.to_dict(),
                            "backend_config": config.to_dict()}),
                encoding="utf-8",
            )
            output = root / "runs"
            printed = StringIO()
            def fake_backend():
                return AirSimBackend(
                    lambda **options: AirSimLegacyAdapter(
                        rpc_factory=lambda host, port: rpc, **options
                    )
                )
            with patch("backends.airsim.AirSimBackend", side_effect=fake_backend):
                with redirect_stdout(printed):
                    exit_code = main([
                        "--config", str(config_file), "--agent", "fixed",
                        "--output", str(output), "--enable-airsim",
                    ])
            self.assertEqual(exit_code, 1)
            printed_result = json.loads(printed.getvalue())
            replay = read_episode(output / printed_result["episode_id"])
            self.assertEqual(replay.result.termination_reason, TerminationReason.BACKEND_ERROR)
            self.assertFalse(replay.result.success)
            self.assertTrue(replay.result.cleanup.succeeded)
            self.assertEqual(len(replay.steps), 1)
            self.assertEqual(replay.steps[0].execution.error.code, "simulator")


if __name__ == "__main__":
    unittest.main()
