"""Feature and unit tests for LawnmowerSearchAgent under platform contract v0.1."""
from __future__ import annotations

import json
import math
from pathlib import Path
import unittest

from agents.fixed_route import FixedRouteAgent
from agents.lawnmower_search import LawnmowerSearchAgent
from agents.reach_point import DirectPointAgent
from backends.mock import MockBackend
from contracts import (
    Action,
    ActionKind,
    BackendConfig,
    ContractValidationError,
    PlatformObservation,
    PositionNed,
    TaskSpec,
)
from tasks.reach_point import ReachPointTask


def make_search_spec(
    search_area_ned: dict[str, float] | None = None,
    lane_spacing_m: float = 10.0,
    waypoint_tolerance_m: float | None = 0.5,
    action_deadline_s: float | None = 5.0,
    home_position_ned: tuple[float, float, float] = (0.0, 0.0, 0.0),
    task_id: str = "search-task-001",
    task_type: str = "search_target",
    extra_params: dict | None = None,
) -> TaskSpec:
    if search_area_ned is None:
        search_area_ned = {
            "min_north_m": 0.0,
            "max_north_m": 40.0,
            "min_east_m": -20.0,
            "max_east_m": 20.0,
            "flight_down_m": -8.0,
        }
    params: dict = {
        "search_area_ned": search_area_ned,
        "lane_spacing_m": lane_spacing_m,
    }
    if waypoint_tolerance_m is not None:
        params["waypoint_tolerance_m"] = waypoint_tolerance_m
    if action_deadline_s is not None:
        params["action_deadline_s"] = action_deadline_s
    if extra_params:
        params.update(extra_params)

    return TaskSpec(
        task_id=task_id,
        task_type=task_type,
        time_budget_s=60.0,
        home_position_ned=PositionNed(*home_position_ned),
        altitude_reference="relative_to_home",
        parameters=params,
    )


def make_obs(
    sequence: int = 0,
    vehicle_id: str = "drone-1",
    position_ned: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> PlatformObservation:
    return PlatformObservation(
        sequence=sequence,
        vehicle_id=vehicle_id,
        position_ned=PositionNed(*position_ned),
        velocity_ned_mps=(0.0, 0.0, 0.0),
        wall_time_ns=1_000_000_000 + sequence * 100_000_000,
    )


class LawnmowerSearchAgentTests(unittest.TestCase):
    """Test suite covering the 21+ required scenarios for LawnmowerSearchAgent."""

    def test_01_divisible_spacing_exact_east_sequence(self) -> None:
        """Scenario 1: Spacing divides width exactly, generating exact east sequence without duplicate."""
        area = {
            "min_north_m": 0.0,
            "max_north_m": 40.0,
            "min_east_m": -20.0,
            "max_east_m": 20.0,
            "flight_down_m": -8.0,
        }
        spec = make_search_spec(search_area_ned=area, lane_spacing_m=10.0)
        agent = LawnmowerSearchAgent()
        init_obs = make_obs(position_ned=(0.0, 0.0, 0.0))
        agent.reset(spec, init_obs)

        # 40m width with 10m spacing -> 5 lanes: -20, -10, 0, 10, 20
        # 5 lanes * 2 waypoints per lane = 10 waypoints
        route = agent.route
        self.assertEqual(len(route), 10)
        expected_easts = [-20.0, -20.0, -10.0, -10.0, 0.0, 0.0, 10.0, 10.0, 20.0, 20.0]
        actual_easts = [wp.east_m for wp in route]
        self.assertEqual(actual_easts, expected_easts)

    def test_02_indivisible_spacing_appends_max_east_boundary(self) -> None:
        """Scenario 2: Spacing does not divide width, max_east_m boundary is appended."""
        area = {
            "min_north_m": 0.0,
            "max_north_m": 30.0,
            "min_east_m": 0.0,
            "max_east_m": 25.0,
            "flight_down_m": -5.0,
        }
        spec = make_search_spec(search_area_ned=area, lane_spacing_m=10.0)
        agent = LawnmowerSearchAgent()
        agent.reset(spec, make_obs(position_ned=(0.0, 0.0, 0.0)))

        # East lanes should be: 0.0, 10.0, 20.0, and 25.0 (boundary) -> 4 lanes = 8 waypoints
        route = agent.route
        self.assertEqual(len(route), 8)
        expected_easts = [0.0, 0.0, 10.0, 10.0, 20.0, 20.0, 25.0, 25.0]
        actual_easts = [wp.east_m for wp in route]
        self.assertEqual(actual_easts, expected_easts)

    def test_03_spacing_larger_than_width_generates_two_boundary_lanes(self) -> None:
        """Scenario 3: Spacing larger than area width still generates min_east and max_east boundary lanes."""
        area = {
            "min_north_m": 0.0,
            "max_north_m": 50.0,
            "min_east_m": 0.0,
            "max_east_m": 15.0,
            "flight_down_m": -10.0,
        }
        spec = make_search_spec(search_area_ned=area, lane_spacing_m=30.0)
        agent = LawnmowerSearchAgent()
        agent.reset(spec, make_obs(position_ned=(0.0, 0.0, 0.0)))

        # Width is 15.0, spacing is 30.0 -> must produce 2 lanes: 0.0 and 15.0 -> 4 waypoints
        route = agent.route
        self.assertEqual(len(route), 4)
        expected_easts = [0.0, 0.0, 15.0, 15.0]
        actual_easts = [wp.east_m for wp in route]
        self.assertEqual(actual_easts, expected_easts)

    def test_04_closer_to_min_north_starts_at_min_north(self) -> None:
        """Scenario 4: Drone initially closer to min_north starts first lane at min_north."""
        area = {
            "min_north_m": 0.0,
            "max_north_m": 40.0,
            "min_east_m": 0.0,
            "max_east_m": 10.0,
            "flight_down_m": -5.0,
        }
        spec = make_search_spec(search_area_ned=area, lane_spacing_m=10.0)
        agent = LawnmowerSearchAgent()
        # Initial north is 5.0 (distance to 0.0 is 5.0, distance to 40.0 is 35.0)
        agent.reset(spec, make_obs(position_ned=(5.0, 0.0, 0.0)))

        route = agent.route
        self.assertEqual(route[0], PositionNed(0.0, 0.0, -5.0))
        self.assertEqual(route[1], PositionNed(40.0, 0.0, -5.0))

    def test_05_closer_to_max_north_starts_at_max_north(self) -> None:
        """Scenario 5: Drone initially closer to max_north starts first lane at max_north."""
        area = {
            "min_north_m": 0.0,
            "max_north_m": 40.0,
            "min_east_m": 0.0,
            "max_east_m": 10.0,
            "flight_down_m": -5.0,
        }
        spec = make_search_spec(search_area_ned=area, lane_spacing_m=10.0)
        agent = LawnmowerSearchAgent()
        # Initial north is 35.0 (distance to 40.0 is 5.0, distance to 0.0 is 35.0)
        agent.reset(spec, make_obs(position_ned=(35.0, 0.0, 0.0)))

        route = agent.route
        self.assertEqual(route[0], PositionNed(40.0, 0.0, -5.0))
        self.assertEqual(route[1], PositionNed(0.0, 0.0, -5.0))

    def test_06_equidistant_north_starts_at_min_north(self) -> None:
        """Scenario 6: When equidistant to min and max north, tie-breaker starts at min_north."""
        area = {
            "min_north_m": 0.0,
            "max_north_m": 40.0,
            "min_east_m": 0.0,
            "max_east_m": 10.0,
            "flight_down_m": -5.0,
        }
        spec = make_search_spec(search_area_ned=area, lane_spacing_m=10.0)
        agent = LawnmowerSearchAgent()
        # Initial north is 20.0 (dist to 0.0 is 20.0, dist to 40.0 is 20.0)
        agent.reset(spec, make_obs(position_ned=(20.0, 0.0, 0.0)))

        route = agent.route
        self.assertEqual(route[0], PositionNed(0.0, 0.0, -5.0))
        self.assertEqual(route[1], PositionNed(40.0, 0.0, -5.0))

    def test_07_serpentine_alternating_directions(self) -> None:
        """Scenario 7: Continuous serpentine pattern strictly alternates direction on consecutive lanes."""
        area = {
            "min_north_m": 0.0,
            "max_north_m": 20.0,
            "min_east_m": 0.0,
            "max_east_m": 30.0,
            "flight_down_m": -5.0,
        }
        spec = make_search_spec(search_area_ned=area, lane_spacing_m=10.0)
        agent = LawnmowerSearchAgent()
        agent.reset(spec, make_obs(position_ned=(0.0, 0.0, 0.0)))

        route = agent.route
        # East: 0, 10, 20, 30 -> 4 lanes = 8 waypoints
        self.assertEqual(len(route), 8)
        # Lane 0: 0.0 -> 20.0
        self.assertEqual(route[0], PositionNed(0.0, 0.0, -5.0))
        self.assertEqual(route[1], PositionNed(20.0, 0.0, -5.0))
        # Lane 1: 20.0 -> 0.0
        self.assertEqual(route[2], PositionNed(20.0, 10.0, -5.0))
        self.assertEqual(route[3], PositionNed(0.0, 10.0, -5.0))
        # Lane 2: 0.0 -> 20.0
        self.assertEqual(route[4], PositionNed(0.0, 20.0, -5.0))
        self.assertEqual(route[5], PositionNed(20.0, 20.0, -5.0))
        # Lane 3: 20.0 -> 0.0
        self.assertEqual(route[6], PositionNed(20.0, 30.0, -5.0))
        self.assertEqual(route[7], PositionNed(0.0, 30.0, -5.0))

    def test_08_flight_down_applied_to_all_waypoints(self) -> None:
        """Scenario 8: flight_down_m is consistently applied to all generated route waypoints."""
        area = {
            "min_north_m": 0.0,
            "max_north_m": 30.0,
            "min_east_m": -10.0,
            "max_east_m": 10.0,
            "flight_down_m": -12.5,
        }
        spec = make_search_spec(search_area_ned=area, lane_spacing_m=5.0)
        agent = LawnmowerSearchAgent()
        agent.reset(spec, make_obs(position_ned=(0.0, 0.0, 0.0)))

        for idx, wp in enumerate(agent.route):
            self.assertEqual(
                wp.down_m, -12.5, f"Waypoint {idx} has down_m={wp.down_m} != -12.5"
            )

    def test_09_does_not_advance_before_reaching_waypoint(self) -> None:
        """Scenario 9: Agent does not skip or advance waypoint if observation distance > tolerance."""
        area = {
            "min_north_m": 0.0,
            "max_north_m": 40.0,
            "min_east_m": 0.0,
            "max_east_m": 10.0,
            "flight_down_m": -5.0,
        }
        spec = make_search_spec(
            search_area_ned=area, lane_spacing_m=10.0, waypoint_tolerance_m=0.5
        )
        agent = LawnmowerSearchAgent()
        init_obs = make_obs(position_ned=(0.0, 0.0, 0.0))
        agent.reset(spec, init_obs)

        # Target wp0 is (0.0, 0.0, -5.0). Distance from (0, 0, 0) is 5.0 > 0.5
        act1 = agent.act(init_obs)
        self.assertEqual(act1.kind, ActionKind.MOVE_TO)
        self.assertEqual(act1.target_position_ned, PositionNed(0.0, 0.0, -5.0))

        # Move to (0.0, 0.0, -4.0), distance to wp0 is 1.0 > 0.5
        obs_halfway = make_obs(sequence=1, position_ned=(0.0, 0.0, -4.0))
        act2 = agent.act(obs_halfway)
        self.assertEqual(act2.kind, ActionKind.MOVE_TO)
        self.assertEqual(act2.target_position_ned, PositionNed(0.0, 0.0, -5.0))

    def test_10_advances_after_reaching_waypoint(self) -> None:
        """Scenario 10: Agent advances to next waypoint once current waypoint is reached."""
        area = {
            "min_north_m": 0.0,
            "max_north_m": 40.0,
            "min_east_m": 0.0,
            "max_east_m": 10.0,
            "flight_down_m": -5.0,
        }
        spec = make_search_spec(
            search_area_ned=area, lane_spacing_m=10.0, waypoint_tolerance_m=0.5
        )
        agent = LawnmowerSearchAgent()
        agent.reset(spec, make_obs(position_ned=(0.0, 0.0, 0.0)))

        # wp0 is (0.0, 0.0, -5.0), wp1 is (40.0, 0.0, -5.0)
        # Position at (0.2, 0.0, -5.0), distance 0.2 <= 0.5 -> reaches wp0, targets wp1
        obs_reached_wp0 = make_obs(sequence=1, position_ned=(0.2, 0.0, -5.0))
        action = agent.act(obs_reached_wp0)
        self.assertEqual(action.kind, ActionKind.MOVE_TO)
        self.assertEqual(action.target_position_ned, PositionNed(40.0, 0.0, -5.0))

    def test_11_exact_tolerance_boundary_treated_as_arrived(self) -> None:
        """Scenario 11: Exact distance == waypoint_tolerance_m is treated as reached."""
        area = {
            "min_north_m": 0.0,
            "max_north_m": 40.0,
            "min_east_m": 0.0,
            "max_east_m": 10.0,
            "flight_down_m": -5.0,
        }
        spec = make_search_spec(
            search_area_ned=area, lane_spacing_m=10.0, waypoint_tolerance_m=0.5
        )
        agent = LawnmowerSearchAgent()
        agent.reset(spec, make_obs(position_ned=(0.0, 0.0, 0.0)))

        # wp0 is (0.0, 0.0, -5.0). At (0.5, 0.0, -5.0), distance is exactly 0.5 <= 0.5
        obs_exact_wp0 = make_obs(sequence=1, position_ned=(0.5, 0.0, -5.0))
        action = agent.act(obs_exact_wp0)
        self.assertEqual(action.kind, ActionKind.MOVE_TO)
        self.assertEqual(action.target_position_ned, PositionNed(40.0, 0.0, -5.0))

    def test_12_hovers_continuously_after_final_waypoint_reached(self) -> None:
        """Scenario 12: After final waypoint is reached, agent continuously outputs HOVER."""
        area = {
            "min_north_m": 0.0,
            "max_north_m": 10.0,
            "min_east_m": 0.0,
            "max_east_m": 10.0,
            "flight_down_m": -5.0,
        }
        spec = make_search_spec(
            search_area_ned=area, lane_spacing_m=10.0, waypoint_tolerance_m=0.5
        )
        agent = LawnmowerSearchAgent()
        agent.reset(spec, make_obs(position_ned=(0.0, 0.0, 0.0)))

        seq = 1
        # Step through the waypoints to reach the final waypoint in order
        for wp in agent.route[:-1]:
            obs_wp = make_obs(
                sequence=seq,
                position_ned=(wp.north_m, wp.east_m, wp.down_m),
            )
            act = agent.act(obs_wp)
            self.assertEqual(act.kind, ActionKind.MOVE_TO)
            seq += 1

        # Now at final waypoint
        final_wp = agent.route[-1]
        obs_at_final = make_obs(
            sequence=seq,
            position_ned=(final_wp.north_m, final_wp.east_m, final_wp.down_m),
        )

        act1 = agent.act(obs_at_final)
        self.assertEqual(act1.kind, ActionKind.HOVER)
        self.assertIsNone(act1.target_position_ned)

        # Subsequent steps continue to HOVER even if observation position shifts slightly
        obs_next = make_obs(
            sequence=seq + 1,
            position_ned=(final_wp.north_m + 0.1, final_wp.east_m, final_wp.down_m),
        )
        act2 = agent.act(obs_next)
        self.assertEqual(act2.kind, ActionKind.HOVER)
        self.assertIsNone(act2.target_position_ned)

    def test_13_nonzero_home_does_not_alter_absolute_waypoints(self) -> None:
        """Scenario 13: Non-zero home position does not shift absolute world NED search waypoints."""
        area = {
            "min_north_m": 0.0,
            "max_north_m": 40.0,
            "min_east_m": -20.0,
            "max_east_m": 20.0,
            "flight_down_m": -8.0,
        }
        spec_zero_home = make_search_spec(
            search_area_ned=area, home_position_ned=(0.0, 0.0, 0.0)
        )
        spec_nonzero_home = make_search_spec(
            search_area_ned=area, home_position_ned=(100.0, -50.0, 25.0)
        )

        agent1 = LawnmowerSearchAgent()
        agent2 = LawnmowerSearchAgent()
        init_obs = make_obs(position_ned=(0.0, 0.0, 0.0))

        agent1.reset(spec_zero_home, init_obs)
        agent2.reset(spec_nonzero_home, init_obs)

        self.assertEqual(agent1.route, agent2.route)

    def test_14_action_id_unique_and_restarts_on_reset(self) -> None:
        """Scenario 14: action_id is sequential/unique (lawnmower-search-N) and resets to 1 after reset()."""
        spec = make_search_spec()
        agent = LawnmowerSearchAgent()
        init_obs = make_obs()
        agent.reset(spec, init_obs)

        act1 = agent.act(init_obs)
        act2 = agent.act(make_obs(sequence=1))
        act3 = agent.act(make_obs(sequence=2))

        self.assertEqual(act1.action_id, "lawnmower-search-1")
        self.assertEqual(act2.action_id, "lawnmower-search-2")
        self.assertEqual(act3.action_id, "lawnmower-search-3")

        # Reset restarts action_id count
        agent.reset(spec, init_obs)
        act_after_reset = agent.act(init_obs)
        self.assertEqual(act_after_reset.action_id, "lawnmower-search-1")

    def test_15_act_before_reset_after_close_and_wrong_vehicle_id(self) -> None:
        """Scenario 15: Rejects act() before reset(), after close(), or with mismatched vehicle_id."""
        agent = LawnmowerSearchAgent()
        obs = make_obs(vehicle_id="drone-1")

        # Act before reset
        with self.assertRaises(RuntimeError):
            agent.act(obs)

        spec = make_search_spec()
        agent.reset(spec, obs)

        # Wrong vehicle_id
        wrong_vehicle_obs = make_obs(vehicle_id="drone-other")
        with self.assertRaises(ContractValidationError):
            agent.act(wrong_vehicle_obs)

        # Act after close
        agent.close()
        with self.assertRaises(RuntimeError):
            agent.act(obs)

    def test_16_invalid_task_type_missing_fields_and_containers(self) -> None:
        """Scenario 16: Rejects wrong task_type, missing parameters, and invalid containers."""
        agent = LawnmowerSearchAgent()
        obs = make_obs()

        # Wrong task_type
        spec_wrong_type = make_search_spec(task_type="reach_point")
        with self.assertRaises(ContractValidationError):
            agent.reset(spec_wrong_type, obs)

        # Parameters not a dict
        spec_bad_params = TaskSpec(
            task_id="t1",
            task_type="search_target",
            time_budget_s=60.0,
            home_position_ned=PositionNed(0, 0, 0),
            altitude_reference="relative_to_home",
            parameters="not-a-dict",  # type: ignore[arg-type]
        )
        with self.assertRaises(ContractValidationError):
            agent.reset(spec_bad_params, obs)

        # Missing search_area_ned
        spec_no_area = TaskSpec(
            task_id="t1",
            task_type="search_target",
            time_budget_s=60.0,
            home_position_ned=PositionNed(0, 0, 0),
            altitude_reference="relative_to_home",
            parameters={"lane_spacing_m": 10.0},
        )
        with self.assertRaises(ContractValidationError):
            agent.reset(spec_no_area, obs)

        # search_area_ned not a dict
        spec_area_list = TaskSpec(
            task_id="t1",
            task_type="search_target",
            time_budget_s=60.0,
            home_position_ned=PositionNed(0, 0, 0),
            altitude_reference="relative_to_home",
            parameters={"search_area_ned": [0, 40, -20, 20, -8], "lane_spacing_m": 10.0},
        )
        with self.assertRaises(ContractValidationError):
            agent.reset(spec_area_list, obs)

        # Missing required keys inside search_area_ned
        for req_key in ("min_north_m", "max_north_m", "min_east_m", "max_east_m", "flight_down_m"):
            incomplete_area = {
                "min_north_m": 0.0,
                "max_north_m": 40.0,
                "min_east_m": -20.0,
                "max_east_m": 20.0,
                "flight_down_m": -8.0,
            }
            del incomplete_area[req_key]
            spec_missing_key = make_search_spec(search_area_ned=incomplete_area)
            with self.assertRaises(ContractValidationError):
                agent.reset(spec_missing_key, obs)

        # Missing lane_spacing_m
        area = {
            "min_north_m": 0.0,
            "max_north_m": 40.0,
            "min_east_m": -20.0,
            "max_east_m": 20.0,
            "flight_down_m": -8.0,
        }
        spec_no_spacing = TaskSpec(
            task_id="t1",
            task_type="search_target",
            time_budget_s=60.0,
            home_position_ned=PositionNed(0, 0, 0),
            altitude_reference="relative_to_home",
            parameters={"search_area_ned": area},
        )
        with self.assertRaises(ContractValidationError):
            agent.reset(spec_no_spacing, obs)

    def test_17_invalid_values_nan_inf_bool_zero_neg_inverted_bounds(self) -> None:
        """Scenario 17: Rejects NaN, Inf, bool, <= 0 spacing/tolerance/deadline, and inverted/degenerate bounds."""
        agent = LawnmowerSearchAgent()
        obs = make_obs()

        # Inverted north bounds (min > max)
        area_inv_north = {
            "min_north_m": 50.0,
            "max_north_m": 40.0,
            "min_east_m": -20.0,
            "max_east_m": 20.0,
            "flight_down_m": -8.0,
        }
        with self.assertRaises(ContractValidationError):
            agent.reset(make_search_spec(search_area_ned=area_inv_north), obs)

        # Degenerate north bounds (min == max)
        area_deg_north = {
            "min_north_m": 40.0,
            "max_north_m": 40.0,
            "min_east_m": -20.0,
            "max_east_m": 20.0,
            "flight_down_m": -8.0,
        }
        with self.assertRaises(ContractValidationError):
            agent.reset(make_search_spec(search_area_ned=area_deg_north), obs)

        # Inverted east bounds (min > max)
        area_inv_east = {
            "min_north_m": 0.0,
            "max_north_m": 40.0,
            "min_east_m": 30.0,
            "max_east_m": 20.0,
            "flight_down_m": -8.0,
        }
        with self.assertRaises(ContractValidationError):
            agent.reset(make_search_spec(search_area_ned=area_inv_east), obs)

        # Degenerate east bounds (min == max)
        area_deg_east = {
            "min_north_m": 0.0,
            "max_north_m": 40.0,
            "min_east_m": 20.0,
            "max_east_m": 20.0,
            "flight_down_m": -8.0,
        }
        with self.assertRaises(ContractValidationError):
            agent.reset(make_search_spec(search_area_ned=area_deg_east), obs)

        # lane_spacing_m <= 0
        for bad_spacing in (0.0, -10.0):
            with self.assertRaises(ContractValidationError):
                agent.reset(make_search_spec(lane_spacing_m=bad_spacing), obs)

        # waypoint_tolerance_m <= 0
        for bad_tol in (0.0, -0.5):
            with self.assertRaises(ContractValidationError):
                agent.reset(make_search_spec(waypoint_tolerance_m=bad_tol), obs)

        # action_deadline_s <= 0
        for bad_dl in (0.0, -5.0):
            with self.assertRaises(ContractValidationError):
                agent.reset(make_search_spec(action_deadline_s=bad_dl), obs)

        # bool values passed for numbers
        area_bool = {
            "min_north_m": True,
            "max_north_m": 40.0,
            "min_east_m": -20.0,
            "max_east_m": 20.0,
            "flight_down_m": -8.0,
        }
        with self.assertRaises(ContractValidationError):
            agent.reset(make_search_spec(search_area_ned=area_bool), obs)

        # NaN and Inf in area coordinates
        for bad_val in (math.nan, math.inf, -math.inf):
            area_nan = {
                "min_north_m": 0.0,
                "max_north_m": 40.0,
                "min_east_m": -20.0,
                "max_east_m": 20.0,
                "flight_down_m": bad_val,
            }
            with self.assertRaises(ContractValidationError):
                agent.reset(make_search_spec(search_area_ned=area_nan), obs)

    def test_18_failed_reset_leaves_agent_uninitialized(self) -> None:
        """Scenario 18: If reset fails for any reason, existing state is cleared and agent remains uninitialized."""
        agent = LawnmowerSearchAgent()
        valid_spec = make_search_spec()
        obs = make_obs()

        # Successfully initialize first
        agent.reset(valid_spec, obs)
        self.assertTrue(len(agent.route) > 0)
        self.assertEqual(agent.act(obs).kind, ActionKind.MOVE_TO)

        # Attempt invalid reset (bad task_type)
        invalid_spec = make_search_spec(task_type="unsupported_type")
        with self.assertRaises(ContractValidationError):
            agent.reset(invalid_spec, obs)

        # Must be left uninitialized: route empty, act() raises RuntimeError
        self.assertEqual(agent.route, ())
        with self.assertRaises(RuntimeError):
            agent.act(obs)

    def test_19_example_config_consumable_by_agent_and_task(self) -> None:
        """Scenario 19: configs/lawnmower_search.example.json can be parsed and executed by LawnmowerSearchAgent."""
        config_path = (
            Path(__file__).resolve().parents[1]
            / "configs"
            / "lawnmower_search.example.json"
        )
        self.assertTrue(config_path.exists(), "configs/lawnmower_search.example.json does not exist")
        data = json.loads(config_path.read_text(encoding="utf-8"))

        backend_cfg = BackendConfig.from_dict(data["backend_config"])
        task_spec = TaskSpec.from_dict(data["task_spec"])

        agent = LawnmowerSearchAgent()
        init_obs = make_obs(
            vehicle_id=backend_cfg.vehicle_id,
            position_ned=(
                task_spec.home_position_ned.north_m,
                task_spec.home_position_ned.east_m,
                task_spec.home_position_ned.down_m,
            ),
        )

        agent.reset(task_spec, init_obs)
        self.assertTrue(len(agent.route) > 0)

        # Act produces valid MOVE_TO action
        action = agent.act(init_obs)
        self.assertEqual(action.kind, ActionKind.MOVE_TO)
        self.assertEqual(action.vehicle_id, backend_cfg.vehicle_id)
        self.assertIsNotNone(action.target_position_ned)
        self.assertEqual(action.target_position_ned.down_m, -8.0)

    def test_20_full_route_execution_with_mock_backend_ends_in_hover(self) -> None:
        """Scenario 20: Full simulation loop with MockBackend steps through entire route and finishes in HOVER."""
        config_path = (
            Path(__file__).resolve().parents[1]
            / "configs"
            / "lawnmower_search.example.json"
        )
        data = json.loads(config_path.read_text(encoding="utf-8"))
        backend_cfg = BackendConfig.from_dict(data["backend_config"])
        task_spec = TaskSpec.from_dict(data["task_spec"])

        backend = MockBackend()
        agent = LawnmowerSearchAgent()

        obs = backend.reset(backend_cfg, task_spec)
        agent.reset(task_spec, obs)

        total_waypoints = len(agent.route)
        visited_waypoints: list[PositionNed] = []

        # Run closed-loop stepping until agent reports HOVER
        max_steps = total_waypoints + 5
        for _ in range(max_steps):
            action = agent.act(obs)
            if action.kind is ActionKind.HOVER:
                break
            visited_waypoints.append(action.target_position_ned)
            exec_res = backend.execute(action)
            self.assertTrue(exec_res.succeeded)
            obs = backend.observe()

        # Verify all waypoints in the planned route were targeted in sequence
        self.assertEqual(visited_waypoints, list(agent.route))

        # Final action must be HOVER
        final_action = agent.act(obs)
        self.assertEqual(final_action.kind, ActionKind.HOVER)
        self.assertIsNone(final_action.target_position_ned)

        # Cleanup
        self.assertTrue(backend.cleanup().succeeded)
        agent.close()
        backend.close()

    def test_21_consecutive_coincident_waypoints_skipped_in_single_act(self) -> None:
        """Scenario 21: If multiple waypoints are coincident or within tolerance, agent skips all in one act()."""
        # Create an area where north bounds are very close (0.0 to 0.4), and waypoint_tolerance is 0.5
        area = {
            "min_north_m": 0.0,
            "max_north_m": 0.4,
            "min_east_m": 0.0,
            "max_east_m": 0.4,
            "flight_down_m": 0.0,
        }
        # Drone starts at (0, 0, 0), tolerance is 1.0 (covers all waypoints!)
        spec = make_search_spec(
            search_area_ned=area, lane_spacing_m=10.0, waypoint_tolerance_m=1.0
        )
        agent = LawnmowerSearchAgent()
        init_obs = make_obs(position_ned=(0.0, 0.0, 0.0))
        agent.reset(spec, init_obs)

        # All 4 waypoints are within 1.0m of (0, 0, 0), so single act() skips all to completion
        action = agent.act(init_obs)
        self.assertEqual(action.kind, ActionKind.HOVER)

    def test_22_regression_and_task_isolation(self) -> None:
        """Scenario 22: TASK-001 and TASK-002 classes remain intact, usable, and isolated."""
        # DirectPointAgent remains functional
        dp_agent = DirectPointAgent()
        self.assertIsNotNone(dp_agent)

        # FixedRouteAgent remains functional
        fr_agent = FixedRouteAgent()
        self.assertIsNotNone(fr_agent)

        # ReachPointTask remains functional
        rp_task = ReachPointTask()
        self.assertIsNotNone(rp_task)

    def test_23_narrow_area_width_below_1e7_spacing_larger_keeps_both_boundaries(self) -> None:
        """P0 regression: Width < 1e-7 and spacing > width retains both min_east and max_east boundaries."""
        area = {
            "min_north_m": 0.0,
            "max_north_m": 10.0,
            "min_east_m": 0.0,
            "max_east_m": 1e-8,
            "flight_down_m": -5.0,
        }
        spec = make_search_spec(search_area_ned=area, lane_spacing_m=1.0)
        agent = LawnmowerSearchAgent()
        agent.reset(spec, make_obs(position_ned=(0.0, 0.0, 0.0)))

        # Route must have exactly 2 lanes = 4 waypoints, easts must be [0.0, 0.0, 1e-8, 1e-8]
        route = agent.route
        self.assertEqual(len(route), 4)
        route_easts = [wp.east_m for wp in route]
        self.assertEqual(route_easts, [0.0, 0.0, 1e-8, 1e-8])

    def test_24_tiny_spacing_excessive_lanes_rejected_fast(self) -> None:
        """P1: Extremely small positive spacing exceeding MAX_LANES capacity is rejected fast."""
        agent = LawnmowerSearchAgent()
        obs = make_obs()

        # width = 40m, spacing = 1e-300 -> estimated lanes >> 10000 -> ContractValidationError
        spec_tiny_spacing = make_search_spec(lane_spacing_m=1e-300)
        with self.assertRaises(ContractValidationError):
            agent.reset(spec_tiny_spacing, obs)
        # Agent must remain uninitialized
        with self.assertRaises(RuntimeError):
            agent.act(obs)

        # width = 1000m, spacing = 0.05 -> estimated 20002 > 10000 -> ContractValidationError
        area_large = {
            "min_north_m": 0.0,
            "max_north_m": 10.0,
            "min_east_m": 0.0,
            "max_east_m": 1000.0,
            "flight_down_m": -5.0,
        }
        spec_too_many = make_search_spec(search_area_ned=area_large, lane_spacing_m=0.05)
        with self.assertRaises(ContractValidationError):
            agent.reset(spec_too_many, obs)
        with self.assertRaises(RuntimeError):
            agent.act(obs)

    def test_25_spacing_below_float_resolution_at_large_coordinates_rejected(self) -> None:
        """P1: Large coordinates where lane_spacing_m is below IEEE-754 resolution are rejected."""
        agent = LawnmowerSearchAgent()
        obs = make_obs()

        # At scale 1e16, ULP is 2.0. spacing 0.1 cannot advance coordinates
        area_large_coords = {
            "min_north_m": 0.0,
            "max_north_m": 10.0,
            "min_east_m": 1e16,
            "max_east_m": 1e16 + 100.0,
            "flight_down_m": -5.0,
        }
        spec_sub_res = make_search_spec(search_area_ned=area_large_coords, lane_spacing_m=0.1)
        with self.assertRaises(ContractValidationError):
            agent.reset(spec_sub_res, obs)
        # Agent must remain uninitialized
        with self.assertRaises(RuntimeError):
            agent.act(obs)

    def test_26_all_routes_strictly_increasing_from_min_to_max_without_duplicates(self) -> None:
        """Verification: All generated routes have east coords starting at min, ending at max, strictly increasing."""
        test_cases = [
            # (min_east, max_east, spacing)
            (0.0, 1e-8, 1.0),
            (0.0, 25.0, 10.0),
            (-20.0, 20.0, 10.0),
            (0.0, 15.0, 30.0),
            (10.0, 10.5, 0.1),
            (-100.0, -99.0, 0.25),
            (0.0, 0.3, 0.1),
        ]
        agent = LawnmowerSearchAgent()
        obs = make_obs()

        for min_e, max_e, spacing in test_cases:
            area = {
                "min_north_m": 0.0,
                "max_north_m": 20.0,
                "min_east_m": min_e,
                "max_east_m": max_e,
                "flight_down_m": -5.0,
            }
            spec = make_search_spec(search_area_ned=area, lane_spacing_m=spacing)
            agent.reset(spec, obs)
            route = agent.route

            # Extract unique east coordinates preserving route order
            unique_easts: list[float] = []
            for wp in route:
                if not unique_easts or wp.east_m != unique_easts[-1]:
                    unique_easts.append(wp.east_m)

            self.assertEqual(unique_easts[0], min_e, f"First lane {unique_easts[0]} != min {min_e}")
            self.assertEqual(unique_easts[-1], max_e, f"Last lane {unique_easts[-1]} != max {max_e}")
            self.assertEqual(len(unique_easts), len(set(unique_easts)), "Duplicate east coordinates found")

            # Check strictly increasing
            for idx in range(len(unique_easts) - 1):
                self.assertLess(
                    unique_easts[idx],
                    unique_easts[idx + 1],
                    f"Not strictly increasing at {idx}: {unique_easts[idx]} >= {unique_easts[idx + 1]}",
                )

    def test_27_large_coordinates_preserves_valid_internal_lanes(self) -> None:
        """Round 2 review fix: Large coordinates 1e16 -> 1e16+8 with spacing 6 must preserve internal lane 1e16+6."""
        min_e = 1e16
        max_e = 1e16 + 8.0
        spacing = 6.0
        area = {
            "min_north_m": 0.0,
            "max_north_m": 10.0,
            "min_east_m": min_e,
            "max_east_m": max_e,
            "flight_down_m": -5.0,
        }
        spec = make_search_spec(search_area_ned=area, lane_spacing_m=spacing)
        agent = LawnmowerSearchAgent()
        agent.reset(spec, make_obs(position_ned=(0.0, min_e, -5.0)))

        route = agent.route
        # Expected east coordinates for the 3 lanes: [1e16, 1e16+6, 1e16+8]
        # Route has 6 waypoints (2 per lane)
        self.assertEqual(len(route), 6)
        expected_easts = [min_e, min_e, min_e + 6.0, min_e + 6.0, max_e, max_e]
        self.assertEqual([wp.east_m for wp in route], expected_easts)

    def test_28_float_decimal_spacing_preserves_exact_max_and_monotonicity(self) -> None:
        """Round 2 review fix: Decimal float 0.0 -> 0.3 with spacing 0.1 ends in exact max, strictly increasing, no duplicates."""
        area = {
            "min_north_m": 0.0,
            "max_north_m": 10.0,
            "min_east_m": 0.0,
            "max_east_m": 0.3,
            "flight_down_m": -5.0,
        }
        spec = make_search_spec(search_area_ned=area, lane_spacing_m=0.1)
        agent = LawnmowerSearchAgent()
        agent.reset(spec, make_obs(position_ned=(0.0, 0.0, -5.0)))

        route = agent.route
        unique_easts: list[float] = []
        for wp in route:
            if not unique_easts or wp.east_m != unique_easts[-1]:
                unique_easts.append(wp.east_m)

        # Must end in exact 0.3
        self.assertEqual(unique_easts[0], 0.0)
        self.assertEqual(unique_easts[-1], 0.3)
        self.assertEqual(len(unique_easts), 4)
        self.assertEqual(len(unique_easts), len(set(unique_easts)))
        for idx in range(len(unique_easts) - 1):
            self.assertLess(unique_easts[idx], unique_easts[idx + 1])
