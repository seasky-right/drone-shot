"""Feature and regression tests for FixedRouteAgent under platform contract v0.1."""
from __future__ import annotations

import json
import math
from pathlib import Path
import unittest

from agents.fixed_route import FixedRouteAgent
from backends.mock import MockBackend
from contracts import (
    Action,
    ActionKind,
    BackendConfig,
    ContractValidationError,
    PlatformObservation,
    PositionNed,
    StepRecord,
    TaskSpec,
    TerminationReason,
)
from tasks.reach_point import ReachPointTask


def make_spec(
    task_id: str = "fixed-route-test",
    task_type: str = "reach_point",
    home_ned: tuple[float, float, float] = (10.0, -20.0, 5.0),
    target_ned: tuple[float, float, float] | None = (30.0, 10.0, -10.0),
    intermediate_waypoints: list[dict[str, float]] | object = None,
    tolerance_m: object = 0.5,
    waypoint_tolerance_m: object = None,
    action_deadline_s: object = 5.0,
    include_target: bool = True,
    time_budget_s: float = 60.0,
) -> TaskSpec:
    params: dict[str, object] = {}
    if include_target and target_ned is not None:
        params["target_position_ned"] = {
            "north_m": target_ned[0],
            "east_m": target_ned[1],
            "down_m": target_ned[2],
        }
    if intermediate_waypoints is not None:
        params["intermediate_waypoints_ned"] = intermediate_waypoints
    if tolerance_m is not None:
        params["tolerance_m"] = tolerance_m
    if waypoint_tolerance_m is not None:
        params["waypoint_tolerance_m"] = waypoint_tolerance_m
    if action_deadline_s is not None:
        params["action_deadline_s"] = action_deadline_s

    return TaskSpec(
        task_id=task_id,
        task_type=task_type,
        time_budget_s=time_budget_s,
        home_position_ned=PositionNed(home_ned[0], home_ned[1], home_ned[2]),
        altitude_reference="relative_to_home",
        parameters=params,
    )


def make_obs(
    sequence: int = 0,
    vehicle_id: str = "drone-1",
    position_ned: tuple[float, float, float] = (10.0, -20.0, 5.0),
    velocity_ned_mps: tuple[float, float, float] = (0.0, 0.0, 0.0),
    wall_time_ns: int = 1000,
) -> PlatformObservation:
    return PlatformObservation(
        sequence=sequence,
        vehicle_id=vehicle_id,
        position_ned=PositionNed(position_ned[0], position_ned[1], position_ned[2]),
        velocity_ned_mps=velocity_ned_mps,
        wall_time_ns=wall_time_ns,
    )


class FixedRouteAgentTests(unittest.TestCase):
    """Unit and feature tests for FixedRouteAgent."""

    def test_01_no_intermediate_waypoints_moves_directly_to_target(self) -> None:
        spec = make_spec(target_ned=(30.0, 10.0, -10.0), intermediate_waypoints=None)
        agent = FixedRouteAgent()
        obs = make_obs(position_ned=(0.0, 0.0, 0.0))
        agent.reset(spec, obs)

        action = agent.act(obs)
        self.assertEqual(action.kind, ActionKind.MOVE_TO)
        self.assertEqual(action.target_position_ned, PositionNed(30.0, 10.0, -10.0))

    def test_02_multiple_waypoints_followed_in_strict_configured_order(self) -> None:
        w1 = {"north_m": 15.0, "east_m": -10.0, "down_m": -2.0}
        w2 = {"north_m": 22.0, "east_m": 0.0, "down_m": -6.0}
        target = (30.0, 10.0, -10.0)
        spec = make_spec(
            target_ned=target,
            intermediate_waypoints=[w1, w2],
            waypoint_tolerance_m=0.5,
            tolerance_m=0.5,
        )
        agent = FixedRouteAgent()
        init_obs = make_obs(position_ned=(0.0, 0.0, 0.0))
        agent.reset(spec, init_obs)

        # 1. At start: should target w1
        act1 = agent.act(init_obs)
        self.assertEqual(act1.kind, ActionKind.MOVE_TO)
        self.assertEqual(act1.target_position_ned, PositionNed(15.0, -10.0, -2.0))

        # 2. At w1: should target w2
        obs_w1 = make_obs(sequence=1, position_ned=(15.0, -10.0, -2.0))
        act2 = agent.act(obs_w1)
        self.assertEqual(act2.kind, ActionKind.MOVE_TO)
        self.assertEqual(act2.target_position_ned, PositionNed(22.0, 0.0, -6.0))

        # 3. At w2: should target final target
        obs_w2 = make_obs(sequence=2, position_ned=(22.0, 0.0, -6.0))
        act3 = agent.act(obs_w2)
        self.assertEqual(act3.kind, ActionKind.MOVE_TO)
        self.assertEqual(act3.target_position_ned, PositionNed(30.0, 10.0, -10.0))

    def test_03_does_not_advance_before_reaching_current_waypoint(self) -> None:
        w1 = {"north_m": 10.0, "east_m": 0.0, "down_m": 0.0}
        w2 = {"north_m": 20.0, "east_m": 0.0, "down_m": 0.0}
        spec = make_spec(target_ned=(30.0, 0.0, 0.0), intermediate_waypoints=[w1, w2], waypoint_tolerance_m=0.5)
        agent = FixedRouteAgent()
        init_obs = make_obs(position_ned=(0.0, 0.0, 0.0))
        agent.reset(spec, init_obs)

        # Distance to w1 is 2.0 > 0.5
        obs_halfway = make_obs(sequence=1, position_ned=(8.0, 0.0, 0.0))
        action = agent.act(obs_halfway)
        self.assertEqual(action.kind, ActionKind.MOVE_TO)
        self.assertEqual(action.target_position_ned, PositionNed(10.0, 0.0, 0.0))

    def test_04_advances_to_next_waypoint_when_current_waypoint_reached(self) -> None:
        w1 = {"north_m": 10.0, "east_m": 0.0, "down_m": 0.0}
        w2 = {"north_m": 20.0, "east_m": 0.0, "down_m": 0.0}
        spec = make_spec(target_ned=(30.0, 0.0, 0.0), intermediate_waypoints=[w1, w2], waypoint_tolerance_m=0.5)
        agent = FixedRouteAgent()
        agent.reset(spec, make_obs())

        # Reach w1 within tolerance (distance 0.2 <= 0.5)
        obs_reached_w1 = make_obs(sequence=1, position_ned=(10.2, 0.0, 0.0))
        action = agent.act(obs_reached_w1)
        self.assertEqual(action.kind, ActionKind.MOVE_TO)
        self.assertEqual(action.target_position_ned, PositionNed(20.0, 0.0, 0.0))

    def test_05_exact_tolerance_boundary_treated_as_arrived(self) -> None:
        w1 = {"north_m": 10.0, "east_m": 0.0, "down_m": 0.0}
        target = (20.0, 0.0, 0.0)
        spec = make_spec(
            target_ned=target,
            intermediate_waypoints=[w1],
            waypoint_tolerance_m=0.5,
            tolerance_m=0.75,
        )
        agent = FixedRouteAgent()
        agent.reset(spec, make_obs())

        # Exact distance 0.5 to w1 -> must advance to target
        obs_exact_w1 = make_obs(sequence=1, position_ned=(10.5, 0.0, 0.0))
        act_w1 = agent.act(obs_exact_w1)
        self.assertEqual(act_w1.kind, ActionKind.MOVE_TO)
        self.assertEqual(act_w1.target_position_ned, PositionNed(20.0, 0.0, 0.0))

        # Exact distance 0.75 to target -> must hover
        obs_exact_target = make_obs(sequence=2, position_ned=(20.75, 0.0, 0.0))
        act_target = agent.act(obs_exact_target)
        self.assertEqual(act_target.kind, ActionKind.HOVER)
        self.assertIsNone(act_target.target_position_ned)

    def test_06_final_target_uses_target_tolerance_not_waypoint_tolerance(self) -> None:
        w1 = {"north_m": 10.0, "east_m": 0.0, "down_m": 0.0}
        target = (20.0, 0.0, 0.0)
        # waypoint tolerance is large (2.0), but target tolerance is small (0.5)
        spec = make_spec(
            target_ned=target,
            intermediate_waypoints=[w1],
            waypoint_tolerance_m=2.0,
            tolerance_m=0.5,
        )
        agent = FixedRouteAgent()
        agent.reset(spec, make_obs())

        # Reach w1
        agent.act(make_obs(sequence=1, position_ned=(10.0, 0.0, 0.0)))

        # At target, distance is 1.0 (<= waypoint_tolerance 2.0, but > target_tolerance 0.5)
        obs_near_target = make_obs(sequence=2, position_ned=(19.0, 0.0, 0.0))
        act = agent.act(obs_near_target)
        # Must NOT hover; must command MOVE_TO target
        self.assertEqual(act.kind, ActionKind.MOVE_TO)
        self.assertEqual(act.target_position_ned, PositionNed(20.0, 0.0, 0.0))

        # Now within target tolerance (dist 0.3 <= 0.5) -> must HOVER
        obs_at_target = make_obs(sequence=3, position_ned=(19.7, 0.0, 0.0))
        act_hover = agent.act(obs_at_target)
        self.assertEqual(act_hover.kind, ActionKind.HOVER)

    def test_07_hovers_after_all_waypoints_and_target_reached(self) -> None:
        target = (10.0, 20.0, -5.0)
        spec = make_spec(target_ned=target, tolerance_m=0.5)
        agent = FixedRouteAgent()
        agent.reset(spec, make_obs())

        obs_at_target = make_obs(position_ned=target)
        act1 = agent.act(obs_at_target)
        act2 = agent.act(obs_at_target)
        self.assertEqual(act1.kind, ActionKind.HOVER)
        self.assertEqual(act2.kind, ActionKind.HOVER)
        self.assertIsNone(act1.target_position_ned)
        self.assertIsNone(act2.target_position_ned)

    def test_08_last_intermediate_waypoint_equal_to_target_does_not_duplicate(self) -> None:
        w1 = {"north_m": 10.0, "east_m": 0.0, "down_m": 0.0}
        target = (20.0, 0.0, 0.0)
        # Last waypoint identical to target
        w2_same_as_target = {"north_m": 20.0, "east_m": 0.0, "down_m": 0.0}
        spec = make_spec(target_ned=target, intermediate_waypoints=[w1, w2_same_as_target])
        agent = FixedRouteAgent()
        agent.reset(spec, make_obs())

        # Route should only have [w1, target], length 2
        self.assertEqual(len(agent._route), 2)
        self.assertEqual(agent._route[0], PositionNed(10.0, 0.0, 0.0))
        self.assertEqual(agent._route[1], PositionNed(20.0, 0.0, 0.0))

        # When at w1, advances directly to target
        act = agent.act(make_obs(sequence=1, position_ned=(10.0, 0.0, 0.0)))
        self.assertEqual(act.target_position_ned, PositionNed(20.0, 0.0, 0.0))

        # When at target, immediately hovers without redundant move_to
        act_final = agent.act(make_obs(sequence=2, position_ned=(20.0, 0.0, 0.0)))
        self.assertEqual(act_final.kind, ActionKind.HOVER)

    def test_09_consecutive_coincident_waypoints_skipped_in_single_act(self) -> None:
        # w1, w2, w3 are coincident at (10, 0, 0), target is (30, 0, 0)
        w1 = {"north_m": 10.0, "east_m": 0.0, "down_m": 0.0}
        w2 = {"north_m": 10.0, "east_m": 0.0, "down_m": 0.0}
        w3 = {"north_m": 10.0, "east_m": 0.0, "down_m": 0.0}
        spec = make_spec(
            target_ned=(30.0, 0.0, 0.0),
            intermediate_waypoints=[w1, w2, w3],
            waypoint_tolerance_m=0.5,
        )
        agent = FixedRouteAgent()
        agent.reset(spec, make_obs())

        # Drone observes it is already at (10, 0, 0)
        obs_at_w = make_obs(sequence=1, position_ned=(10.0, 0.0, 0.0))
        # In a single act(), agent should skip all 3 coincident waypoints and target (30, 0, 0)
        act = agent.act(obs_at_w)
        self.assertEqual(act.kind, ActionKind.MOVE_TO)
        self.assertEqual(act.target_position_ned, PositionNed(30.0, 0.0, 0.0))

    def test_10_action_id_unique_in_episode_and_restarts_on_reset(self) -> None:
        spec = make_spec()
        obs = make_obs()
        agent = FixedRouteAgent()

        agent.reset(spec, obs)
        a1 = agent.act(obs)
        a2 = agent.act(obs)
        self.assertEqual(a1.action_id, "fixed-route-1")
        self.assertEqual(a2.action_id, "fixed-route-2")

        # New episode reset
        agent.reset(spec, obs)
        a_new1 = agent.act(obs)
        self.assertEqual(a_new1.action_id, "fixed-route-1")

    def test_11_nonzero_home_does_not_alter_absolute_waypoint_coordinates(self) -> None:
        home = (500.0, -300.0, 100.0)
        w1 = {"north_m": 10.0, "east_m": 20.0, "down_m": -5.0}
        target = (50.0, 60.0, -10.0)
        spec = make_spec(home_ned=home, target_ned=target, intermediate_waypoints=[w1])
        agent = FixedRouteAgent()
        obs_home = make_obs(position_ned=home)
        agent.reset(spec, obs_home)

        # Agent should move to absolute coordinates (10, 20, -5), NOT shifted by home
        act1 = agent.act(obs_home)
        self.assertEqual(act1.target_position_ned, PositionNed(10.0, 20.0, -5.0))

    def test_12_rejects_act_before_reset_and_after_close(self) -> None:
        agent = FixedRouteAgent()
        obs = make_obs()
        with self.assertRaises(RuntimeError):
            agent.act(obs)

        agent.reset(make_spec(), obs)
        agent.close()
        with self.assertRaises(RuntimeError):
            agent.act(obs)

    def test_13_rejects_observation_with_different_vehicle_id(self) -> None:
        agent = FixedRouteAgent()
        agent.reset(make_spec(), make_obs(vehicle_id="drone-1"))

        with self.assertRaises(ContractValidationError):
            agent.act(make_obs(vehicle_id="drone-2"))

    def test_14_failed_reset_leaves_agent_uninitialized(self) -> None:
        agent = FixedRouteAgent()
        agent.reset(make_spec(target_ned=(10.0, 0.0, 0.0)), make_obs(vehicle_id="drone-1"))
        self.assertEqual(agent.act(make_obs()).kind, ActionKind.MOVE_TO)

        # Second reset with invalid tolerance
        bad_spec = make_spec(tolerance_m=-1.0)
        with self.assertRaises(ContractValidationError):
            agent.reset(bad_spec, make_obs())

        # Must be in uninitialized state
        self.assertIsNone(agent._vehicle_id)
        self.assertEqual(agent._route, ())
        self.assertEqual(agent._action_count, 0)
        with self.assertRaises(RuntimeError):
            agent.act(make_obs())

    def test_15_invalid_containers_and_missing_target_are_rejected(self) -> None:
        init_obs = make_obs()

        # Missing target_position_ned
        with self.assertRaises(ContractValidationError):
            FixedRouteAgent().reset(make_spec(include_target=False), init_obs)

        # Wrong task_type
        with self.assertRaises(ContractValidationError):
            FixedRouteAgent().reset(make_spec(task_type="survey"), init_obs)

        # intermediate_waypoints_ned is not a list (e.g. dict or string)
        with self.assertRaises(ContractValidationError):
            FixedRouteAgent().reset(make_spec(intermediate_waypoints={"w": 1}), init_obs)
        with self.assertRaises(ContractValidationError):
            FixedRouteAgent().reset(make_spec(intermediate_waypoints="invalid"), init_obs)

        # Waypoint inside array is invalid
        bad_waypoint = [{"north_m": "not_a_number", "east_m": 0.0, "down_m": 0.0}]
        with self.assertRaises(ContractValidationError):
            FixedRouteAgent().reset(make_spec(intermediate_waypoints=bad_waypoint), init_obs)

    def test_16_invalid_tolerances_and_deadlines_are_rejected(self) -> None:
        init_obs = make_obs()
        invalid_nums = [0, -1.0, math.nan, math.inf, -math.inf, True, False, "0.5"]

        for val in invalid_nums:
            with self.subTest(invalid_val=val):
                with self.assertRaises(ContractValidationError):
                    spec_tol = make_spec(tolerance_m=val)
                    FixedRouteAgent().reset(spec_tol, init_obs)

                with self.assertRaises(ContractValidationError):
                    spec_wtol = make_spec(waypoint_tolerance_m=val)
                    FixedRouteAgent().reset(spec_wtol, init_obs)

                with self.assertRaises(ContractValidationError):
                    spec_dl = make_spec(action_deadline_s=val)
                    FixedRouteAgent().reset(spec_dl, init_obs)

    def test_17_example_config_consumable_by_agent_and_task(self) -> None:
        config_path = (
            Path(__file__).resolve().parents[1]
            / "configs"
            / "reach_point_fixed_route.example.json"
        )
        self.assertTrue(config_path.exists(), f"{config_path} must exist")

        data = json.loads(config_path.read_text(encoding="utf-8"))
        backend_cfg = BackendConfig.from_dict(data["backend_config"])
        task_spec = TaskSpec.from_dict(data["task_spec"])

        self.assertEqual(backend_cfg.backend_type, "mock")
        self.assertEqual(task_spec.task_type, "reach_point")
        self.assertGreaterEqual(len(task_spec.parameters["intermediate_waypoints_ned"]), 2)

        init_obs = make_obs(
            vehicle_id=backend_cfg.vehicle_id,
            position_ned=(
                task_spec.home_position_ned.north_m,
                task_spec.home_position_ned.east_m,
                task_spec.home_position_ned.down_m,
            ),
        )

        agent = FixedRouteAgent()
        agent.reset(task_spec, init_obs)

        task = ReachPointTask()
        task.reset(task_spec, init_obs)

        # Agent first targets waypoint 1
        first_action = agent.act(init_obs)
        self.assertEqual(first_action.kind, ActionKind.MOVE_TO)
        w1_ned = task_spec.parameters["intermediate_waypoints_ned"][0]
        self.assertEqual(
            first_action.target_position_ned,
            PositionNed(w1_ned["north_m"], w1_ned["east_m"], w1_ned["down_m"]),
        )

    def test_18_multi_step_route_with_mock_backend_and_reach_point_task(self) -> None:
        config_path = (
            Path(__file__).resolve().parents[1]
            / "configs"
            / "reach_point_fixed_route.example.json"
        )
        data = json.loads(config_path.read_text(encoding="utf-8"))
        backend_cfg = BackendConfig.from_dict(data["backend_config"])
        task_spec = TaskSpec.from_dict(data["task_spec"])

        backend = MockBackend()
        agent = FixedRouteAgent()
        task = ReachPointTask()

        obs = backend.reset(backend_cfg, task_spec)
        agent.reset(task_spec, obs)
        task.reset(task_spec, obs)

        # Multi-step loop simulating route execution
        progress = None
        for seq in range(10):
            action = agent.act(obs)
            if action.kind is ActionKind.HOVER:
                break
            exec_res = backend.execute(action)
            self.assertTrue(exec_res.succeeded)
            obs_after = backend.observe()
            step = StepRecord(seq, obs, action, exec_res, obs_after)
            progress = task.update(step)
            obs = obs_after
            if progress.done:
                break

        self.assertIsNotNone(progress)
        self.assertTrue(progress.done)
        self.assertTrue(progress.success)
        self.assertEqual(progress.reason, TerminationReason.SUCCESS)

        # After task success, agent observes it is at final target, should command HOVER
        final_action = agent.act(obs)
        self.assertEqual(final_action.kind, ActionKind.HOVER)
        self.assertIsNone(final_action.target_position_ned)

        # Cleanup
        self.assertTrue(backend.cleanup().succeeded)
        agent.close()
        task.close()
        backend.close()

    def test_19_reach_point_task_and_direct_point_agent_remain_functional(self) -> None:
        from agents.reach_point import DirectPointAgent
        from tasks.reach_point import ReachPointTask

        spec = make_spec()
        obs = make_obs()
        d_agent = DirectPointAgent()
        d_agent.reset(spec, obs)
        self.assertEqual(d_agent.act(obs).kind, ActionKind.MOVE_TO)

        r_task = ReachPointTask()
        r_task.reset(spec, obs)
        self.assertEqual(r_task._vehicle_id, "drone-1")
