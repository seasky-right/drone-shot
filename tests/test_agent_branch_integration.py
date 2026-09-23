"""Run pushed agent/task components through the shared Runner and MockBackend."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agents.reach_point import DirectPointAgent
from backends.mock import MockBackend
from contracts import BackendConfig, TaskSpec
from core.cli import main
from core.recorder import Recorder
from core.replay import read_episode
from core.runner import EpisodeRunner
from evaluators.reachpoint import ReachPointEvaluator
from tasks.reach_point import ReachPointTask as PushedReachPointTask


ROOT = Path(__file__).resolve().parents[1]


class AgentBranchIntegrationTests(unittest.TestCase):
    def test_pushed_agents_run_through_cli_and_recorder(self) -> None:
        scenarios = (
            ("direct", "reach_point.example.json", 1, "DirectPointAgent"),
            ("fixed-route", "reach_point_fixed_route.example.json", 3, "FixedRouteAgent"),
        )
        with TemporaryDirectory() as temporary:
            for agent_name, config_name, expected_steps, agent_type in scenarios:
                with self.subTest(agent=agent_name):
                    output = Path(temporary) / agent_name
                    printed = StringIO()
                    with redirect_stdout(printed):
                        exit_code = main([
                            "--config", str(ROOT / "configs" / config_name),
                            "--agent", agent_name, "--output", str(output),
                        ])
                    self.assertEqual(exit_code, 0)
                    result = json.loads(printed.getvalue())
                    self.assertTrue(result["success"])
                    replay = read_episode(output / result["episode_id"])
                    self.assertEqual(len(replay.steps), expected_steps)
                    metadata = json.loads((replay.directory / "run.json").read_text(encoding="utf-8"))
                    self.assertEqual(metadata["agent_type"], agent_type)

    def test_pushed_task_consumes_runner_step_records(self) -> None:
        config = json.loads((ROOT / "configs" / "reach_point.example.json").read_text(encoding="utf-8"))
        spec = TaskSpec.from_dict(config["task_spec"])
        backend_config = BackendConfig.from_dict(config["backend_config"])
        with TemporaryDirectory() as temporary:
            result = EpisodeRunner(
                MockBackend(), DirectPointAgent(), PushedReachPointTask(),
                ReachPointEvaluator.from_spec(spec), Recorder(temporary),
            ).run(spec, backend_config)
            self.assertTrue(result.success)
            replay = read_episode(Path(temporary) / result.episode_id)
            self.assertEqual(replay.result.termination_reason.value, "success")
            self.assertEqual(len(replay.steps), 1)


if __name__ == "__main__":
    unittest.main()
