"""Track C baseline agents and benchmark pack acceptance."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from agents.reach_point import FixedRouteAgent, RandomAgent, RuleBasedAgent
from contracts import ActionKind, PlatformObservation, PositionNed, TaskSpec
from scripts.validate_reach_point_pack import validate_pack


def spec(seed: int = 101) -> TaskSpec:
    return TaskSpec(
        "baseline-test", "reach_point", 10, PositionNed(0, 0, 0),
        "relative_to_home", {
            "target_position_ned": {"north_m": 10, "east_m": 0, "down_m": 0},
            "tolerance_m": 0.5, "require_return_home": False,
            "require_landing": False, "collision_policy": "zero",
        }, seed=seed)


def observation(sequence: int, north_m: float = 0) -> PlatformObservation:
    return PlatformObservation(sequence, "drone-1", PositionNed(north_m, 0, 0),
                               (0, 0, 0), sequence + 1)


class ReachPointBaselineTests(unittest.TestCase):
    def test_repository_example_uses_complete_reach_point_rules(self) -> None:
        source = json.loads((Path(__file__).resolve().parents[1] /
                             "config.example.json").read_text(encoding="utf-8"))
        task = TaskSpec.from_dict(source["task_spec"])
        agent = RuleBasedAgent(); agent.reset(task, observation(0))
        self.assertIs(agent.act(observation(0)).kind, ActionKind.MOVE_TO)

    def test_random_agent_is_reproducible_by_task_seed(self) -> None:
        first, second = RandomAgent(), RandomAgent()
        first.reset(spec(42), observation(0)); second.reset(spec(42), observation(0))
        self.assertEqual(first.act(observation(0)), second.act(observation(0)))
        self.assertNotEqual(first.act(observation(0)).action_id, first.act(observation(0)).action_id)

    def test_fixed_route_moves_in_order_then_hovers(self) -> None:
        route = (PositionNed(2, 0, 0), PositionNed(10, 0, 0))
        agent = FixedRouteAgent(route); agent.reset(spec(), observation(0))
        self.assertEqual(agent.act(observation(0)).target_position_ned, route[0])
        self.assertEqual(agent.act(observation(1, 2)).target_position_ned, route[1])
        self.assertIs(agent.act(observation(2, 10)).kind, ActionKind.HOVER)

    def test_rule_based_moves_to_goal_then_hovers_after_observed_arrival(self) -> None:
        agent = RuleBasedAgent(); agent.reset(spec(), observation(0))
        action = agent.act(observation(0))
        self.assertIs(action.kind, ActionKind.MOVE_TO)
        self.assertEqual(action.target_position_ned, PositionNed(10, 0, 0))
        self.assertIs(agent.act(observation(1, 9.5)).kind, ActionKind.HOVER)

    def test_benchmark_pack_is_complete_and_importable(self) -> None:
        self.assertEqual(validate_pack(), {
            "benchmark_id": "reach_point_v0_1", "cases": 1,
            "seeds": 5, "agents": 3, "planned_runs": 15,
        })


if __name__ == "__main__":
    unittest.main()
