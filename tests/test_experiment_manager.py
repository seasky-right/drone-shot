from __future__ import annotations

import csv
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agents.mock import FixedMoveAgent, HoverAgent, OffsetMoveAgent
from backends.mock import MockBackend
from contracts import BackendConfig, PositionNed, TaskSpec
from core.experiment import ExperimentManager, ExperimentPlan
from core.experiment_cli import build_manager
from core.plugins import PluginRegistry
from core.replay import read_episode
from evaluators.mock import OneStepPositionEvaluator
from tasks.mock import OneStepPositionTask


TARGET = PositionNed(5, 0, -2)


def plan(*, agents=("fixed", "hover", "offset"), seeds=(3, 7), repeats=2) -> ExperimentPlan:
    return ExperimentPlan(
        TaskSpec(
            "batch-mock", "mock_one_step_position", 5,
            PositionNed(0, 0, 0), "relative_to_home",
            {"target_position_ned": TARGET.to_dict(), "tolerance_m": 0.5},
        ),
        BackendConfig("mock", "Drone"), agents, seeds, repeats, "batch-check",
    )


def manager(root: Path, **extra_agents) -> ExperimentManager:
    factories = {
        "fixed": lambda: FixedMoveAgent(TARGET),
        "hover": HoverAgent,
        "offset": OffsetMoveAgent,
    }
    factories.update(extra_agents)
    return ExperimentManager(
        root, backend_factory=MockBackend, task_factory=OneStepPositionTask,
        evaluator_factory=lambda: OneStepPositionEvaluator(TARGET),
        agent_factories=factories,
    )


class ExperimentManagerTests(unittest.TestCase):
    def test_experiment_snapshot_omits_connection_and_unvalidated_component_config(self) -> None:
        with TemporaryDirectory() as temporary:
            supplied = replace(
                plan(agents=("fixed",), seeds=(1,), repeats=1),
                backend_config=BackendConfig("mock", "Drone", {"password": "backend-secret"}),
                components={"agent": {"id": "example/agent", "config": {"token": "agent-secret"}}},
            )
            result = manager(Path(temporary)).run(supplied)
            recorded = json.loads((result.directory / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(recorded["backend_config"]["connection"], {})
            self.assertIsNone(recorded["components"])
            self.assertNotIn("backend-secret", json.dumps(recorded))
            self.assertNotIn("agent-secret", json.dumps(recorded))

    def test_cli_snapshot_redacts_schema_secret_including_local_reference(self) -> None:
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / "builtin_pack" / "drone_plugin.json").read_text(encoding="utf-8"))
        for component in manifest["components"]:
            if component["id"] == "drone.mock/agent.fixed":
                component["config_schema"] = {
                    "type": "object", "$defs": {"secret": {"type": "string", "writeOnly": True}},
                    "properties": {"token": {"$ref": "#/$defs/secret"}},
                    "additionalProperties": False,
                }
                break
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "drone_plugin.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with patch("core.plugins.metadata.distributions", return_value=[]):
                registry = PluginRegistry.discover(manifest_paths=(path,))
            components = {
                "backend": {"id": "drone.mock/backend"},
                "task": {"id": "drone.mock/task"},
                "evaluator": {"id": "drone.mock/evaluator"},
                "agents": {"fixed": {"id": "drone.mock/agent.fixed",
                                     "config": {"token": "agent-secret"}}},
            }
            supplied = replace(
                plan(agents=("fixed",), seeds=(1,), repeats=1),
                backend_config=BackendConfig("mock", "Drone", {"password": "backend-secret"}),
                components=components,
            )
            with patch("core.experiment_cli.discover_plugins", return_value=registry):
                result = build_manager(supplied, Path(temporary) / "runs").run(supplied)
            recorded = json.loads((result.directory / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(recorded["backend_config"]["connection"], {})
            self.assertEqual(recorded["components"]["agents"]["fixed"]["config"],
                             {"token": "[REDACTED]"})
            self.assertNotIn("backend-secret", json.dumps(recorded))
            self.assertNotIn("agent-secret", json.dumps(recorded))

    def test_three_agents_share_seed_matrix_and_record_all_failures(self) -> None:
        with TemporaryDirectory() as temporary:
            result = manager(Path(temporary)).run(plan())
            self.assertEqual(result.summary["planned_episode_count"], 12)
            self.assertEqual(result.summary["episode_count"], 12)
            self.assertFalse((result.directory / "INCOMPLETE").exists())
            self.assertEqual(result.summary["agents"]["fixed"]["task_success_count"], 4)
            self.assertEqual(result.summary["agents"]["hover"]["task_success_count"], 0)
            self.assertEqual(result.summary["agents"]["offset"]["task_success_count"], 0)
            self.assertEqual(result.summary["agents"]["hover"]["termination_counts"], {"task_failed": 4})
            self.assertEqual(result.summary["agents"]["offset"]["metrics"]["final_distance_m"]["mean"], 1.0)
            with (result.directory / "metrics.csv").open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 12)
            for agent in ("fixed", "hover", "offset"):
                self.assertEqual({(int(row["seed"]), int(row["repeat"])) for row in rows if row["agent"] == agent},
                                 {(3, 1), (3, 2), (7, 1), (7, 2)})
            first = read_episode(result.directory / "episodes" / "episode-000001")
            self.assertEqual(first.task.seed, 3)
            self.assertEqual(first.steps[0].action.action_id, "fixed-move-1")
            self.assertEqual(first.to_dict()["step_count"], 1)
            report = (result.directory / "report.md").read_text(encoding="utf-8")
            self.assertIn("## Numeric metrics", report)
            self.assertIn("Task failures, timeouts and infrastructure errors remain", report)

    def test_factory_error_is_counted_and_does_not_abort_other_agent(self) -> None:
        def fail_factory():
            raise RuntimeError("factory unavailable")
        with TemporaryDirectory() as temporary:
            result = manager(Path(temporary), broken=fail_factory).run(plan(agents=("broken", "fixed"), seeds=(1,), repeats=1))
            self.assertEqual(result.summary["episode_count"], 2)
            self.assertEqual(result.summary["agents"]["broken"]["infrastructure_error_count"], 1)
            self.assertEqual(result.summary["agents"]["fixed"]["task_success_count"], 1)
            with (result.directory / "metrics.csv").open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["termination_reason"], "infrastructure_error")
            self.assertEqual(rows[1]["termination_reason"], "success")
            self.assertFalse((result.directory / "INCOMPLETE").exists())

    def test_plan_and_replay_reject_ambiguous_or_incomplete_data(self) -> None:
        self.assertEqual(ExperimentPlan.from_dict(plan().to_dict()), plan())
        with self.assertRaises(ValueError):
            plan(seeds=(1, 1))
        with self.assertRaises(ValueError):
            ExperimentPlan(plan().task_spec, plan().backend_config, ("fixed",), (1,), 1, "../escape")
        with TemporaryDirectory() as temporary:
            result = manager(Path(temporary)).run(plan(agents=("fixed",), seeds=(2,), repeats=1))
            episode = result.directory / "episodes" / "episode-000001"
            (episode / "INCOMPLETE").write_text("partial", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "incomplete"):
                read_episode(episode)
            (episode / "INCOMPLETE").unlink()
            trajectory = episode / "trajectory.jsonl"
            step = json.loads(trajectory.read_text(encoding="utf-8"))
            step["execution"]["action_id"] = "wrong"
            trajectory.write_text(json.dumps(step) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_episode(episode)


if __name__ == "__main__":
    unittest.main()
