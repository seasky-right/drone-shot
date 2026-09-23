"""Feature tests for formal ReachPointTask and DirectPointAgent under platform contract v0.1."""
from __future__ import annotations

import json
import math
from pathlib import Path
import unittest

from agents.reach_point import DirectPointAgent
from backends.mock import MockBackend
from contracts import (
    Action,
    ActionKind,
    BackendConfig,
    ContractError,
    ContractValidationError,
    ExecutionResult,
    PlatformObservation,
    PositionNed,
    StepRecord,
    TaskProgress,
    TaskSpec,
    TerminationReason,
)
from tasks.reach_point import ReachPointTask


def make_spec(
    task_id: str = "reach-test",
    task_type: str = "reach_point",
    home_ned: tuple[float, float, float] = (10.0, -20.0, 5.0),
    target_ned: tuple[float, float, float] | None = (25.0, -10.0, -8.0),
    tolerance_m: object = 0.5,
    action_deadline_s: object = 5.0,
    include_target: bool = True,
    time_budget_s: float = 30.0,
) -> TaskSpec:
    params: dict[str, object] = {}
    if include_target and target_ned is not None:
        params["target_position_ned"] = {
            "north_m": target_ned[0],
            "east_m": target_ned[1],
            "down_m": target_ned[2],
        }
    if tolerance_m is not None:
        params["tolerance_m"] = tolerance_m
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


def make_observation(
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


def make_step(
    sequence: int,
    obs_before: PlatformObservation,
    obs_after: PlatformObservation,
    succeeded: bool = True,
    action: Action | None = None,
) -> StepRecord:
    if obs_before.sequence != sequence:
        obs_before = PlatformObservation(
            sequence=sequence,
            vehicle_id=obs_before.vehicle_id,
            position_ned=obs_before.position_ned,
            velocity_ned_mps=obs_before.velocity_ned_mps,
            wall_time_ns=obs_before.wall_time_ns,
        )
    if obs_after.sequence != sequence + 1:
        obs_after = PlatformObservation(
            sequence=sequence + 1,
            vehicle_id=obs_after.vehicle_id,
            position_ned=obs_after.position_ned,
            velocity_ned_mps=obs_after.velocity_ned_mps,
            wall_time_ns=obs_after.wall_time_ns,
        )
    if action is None:
        action = Action(
            action_id=f"act-{sequence}",
            kind=ActionKind.MOVE_TO,
            vehicle_id=obs_before.vehicle_id,
            deadline_s=5.0,
            target_position_ned=obs_after.position_ned,
        )
    error = None if succeeded else ContractError("backend_error", "execution failed")
    execution = ExecutionResult(
        action_id=action.action_id,
        accepted=True,
        completed=True,
        succeeded=succeeded,
        wall_time_ns=obs_before.wall_time_ns + 100,
        error=error,
    )
    return StepRecord(
        sequence=sequence,
        observation_before=obs_before,
        action=action,
        execution=execution,
        observation_after=obs_after,
    )


class ReachPointFeatureTests(unittest.TestCase):
    """Unit and feature tests for ReachPointTask and DirectPointAgent."""

    def test_01_agent_reads_target_and_returns_move_to(self) -> None:
        spec = make_spec(target_ned=(30.0, 40.0, -10.0), tolerance_m=1.0)
        agent = DirectPointAgent()
        obs = make_observation(position_ned=(0.0, 0.0, 0.0))
        agent.reset(spec, obs)

        action = agent.act(obs)
        self.assertEqual(action.kind, ActionKind.MOVE_TO)
        self.assertEqual(action.vehicle_id, "drone-1")
        self.assertEqual(action.target_position_ned, PositionNed(30.0, 40.0, -10.0))
        self.assertEqual(action.deadline_s, 5.0)

    def test_02_agent_returns_hover_when_within_tolerance(self) -> None:
        spec = make_spec(target_ned=(10.0, 20.0, -5.0), tolerance_m=0.5)
        agent = DirectPointAgent()
        init_obs = make_observation(position_ned=(0.0, 0.0, 0.0))
        agent.reset(spec, init_obs)

        # Observation within tolerance (distance = 0.3 < 0.5)
        close_obs = make_observation(position_ned=(10.3, 20.0, -5.0))
        action = agent.act(close_obs)
        self.assertEqual(action.kind, ActionKind.HOVER)
        self.assertIsNone(action.target_position_ned)

    def test_03_agent_sequential_action_ids_are_unique_and_deterministic(self) -> None:
        spec = make_spec(target_ned=(10.0, 20.0, -5.0))
        agent = DirectPointAgent()
        obs = make_observation(position_ned=(0.0, 0.0, 0.0))
        agent.reset(spec, obs)

        a1 = agent.act(obs)
        a2 = agent.act(obs)
        a3 = agent.act(obs)
        self.assertNotEqual(a1.action_id, a2.action_id)
        self.assertNotEqual(a2.action_id, a3.action_id)
        self.assertEqual(a1.action_id, "direct-point-1")
        self.assertEqual(a2.action_id, "direct-point-2")
        self.assertEqual(a3.action_id, "direct-point-3")

    def test_04_agent_rejects_act_before_reset(self) -> None:
        agent = DirectPointAgent()
        obs = make_observation()
        with self.assertRaises(RuntimeError):
            agent.act(obs)

    def test_05_agent_rejects_observation_with_different_vehicle_id(self) -> None:
        spec = make_spec()
        agent = DirectPointAgent()
        init_obs = make_observation(vehicle_id="drone-1")
        agent.reset(spec, init_obs)

        wrong_obs = make_observation(vehicle_id="drone-2")
        with self.assertRaises(ContractValidationError):
            agent.act(wrong_obs)

    def test_06_task_uses_post_action_observation_to_judge_success(self) -> None:
        spec = make_spec(target_ned=(10.0, 20.0, -5.0), tolerance_m=0.5)
        task = ReachPointTask()
        init_obs = make_observation(sequence=0, position_ned=(0.0, 0.0, 0.0))
        task.reset(spec, init_obs)

        # Before action: far away. After action: exactly at target.
        obs_before = make_observation(sequence=0, position_ned=(0.0, 0.0, 0.0))
        obs_after = make_observation(sequence=1, position_ned=(10.0, 20.0, -5.0))
        step = make_step(0, obs_before, obs_after, succeeded=True)

        progress = task.update(step)
        self.assertTrue(progress.done)
        self.assertTrue(progress.success)
        self.assertEqual(progress.reason, TerminationReason.SUCCESS)

        # Conversely, if before was at target but after moved away, task must judge as not done
        task.reset(spec, init_obs)
        step_moved_away = make_step(0, obs_after, obs_before, succeeded=True)
        progress2 = task.update(step_moved_away)
        self.assertFalse(progress2.done)
        self.assertFalse(progress2.success)

    def test_07_boundary_distance_exactly_equal_to_tolerance(self) -> None:
        target = (0.0, 0.0, 0.0)
        tolerance = 0.5
        spec = make_spec(target_ned=target, tolerance_m=tolerance)

        # Test Task at exact boundary (dist = 0.5)
        task = ReachPointTask()
        init_obs = make_observation(sequence=0, position_ned=(10.0, 0.0, 0.0))
        task.reset(spec, init_obs)

        obs_before = make_observation(sequence=0, position_ned=(10.0, 0.0, 0.0))
        obs_after = make_observation(sequence=1, position_ned=(0.5, 0.0, 0.0))
        step = make_step(0, obs_before, obs_after, succeeded=True)
        progress = task.update(step)
        self.assertTrue(progress.done)
        self.assertTrue(progress.success)
        self.assertEqual(progress.reason, TerminationReason.SUCCESS)

        # Test Agent at exact boundary (dist = 0.5) -> should HOVER
        agent = DirectPointAgent()
        agent.reset(spec, init_obs)
        action = agent.act(obs_after)
        self.assertEqual(action.kind, ActionKind.HOVER)

    def test_08_task_returns_not_done_when_not_yet_arrived(self) -> None:
        spec = make_spec(target_ned=(10.0, 0.0, 0.0), tolerance_m=0.5)
        task = ReachPointTask()
        init_obs = make_observation(sequence=0, position_ned=(0.0, 0.0, 0.0))
        task.reset(spec, init_obs)

        obs_before = make_observation(sequence=0, position_ned=(0.0, 0.0, 0.0))
        obs_after = make_observation(sequence=1, position_ned=(5.0, 0.0, 0.0))
        step = make_step(0, obs_before, obs_after, succeeded=True)

        progress = task.update(step)
        self.assertFalse(progress.done)
        self.assertFalse(progress.success)
        self.assertIsNone(progress.reason)

    def test_09_backend_execution_failure_returns_backend_error(self) -> None:
        spec = make_spec(target_ned=(10.0, 0.0, 0.0), tolerance_m=0.5)
        task = ReachPointTask()
        init_obs = make_observation(sequence=0, position_ned=(0.0, 0.0, 0.0))
        task.reset(spec, init_obs)

        obs_before = make_observation(sequence=0, position_ned=(0.0, 0.0, 0.0))
        # Even if post-observation reached target, execution failed!
        obs_after = make_observation(sequence=1, position_ned=(10.0, 0.0, 0.0))
        step = make_step(0, obs_before, obs_after, succeeded=False)

        progress = task.update(step)
        self.assertTrue(progress.done)
        self.assertFalse(progress.success)
        self.assertEqual(progress.reason, TerminationReason.BACKEND_ERROR)

    def test_10_nonzero_home_is_not_added_to_absolute_target(self) -> None:
        home = (100.0, -200.0, 50.0)
        target = (5.0, 10.0, -2.0)
        spec = make_spec(home_ned=home, target_ned=target, tolerance_m=0.5)

        # Agent test: target position must be (5, 10, -2), NOT (105, -190, 48)
        agent = DirectPointAgent()
        obs = make_observation(position_ned=home)
        agent.reset(spec, obs)
        action = agent.act(obs)
        self.assertEqual(action.target_position_ned, PositionNed(5.0, 10.0, -2.0))

        # Task test: reaching (5, 10, -2) is success
        task = ReachPointTask()
        task.reset(spec, obs)
        step_correct = make_step(
            0,
            obs,
            make_observation(sequence=1, position_ned=target),
            succeeded=True,
        )
        self.assertTrue(task.update(step_correct).success)

        # Reaching (105, -190, 48) is NOT success
        task.reset(spec, obs)
        step_erroneous_offset = make_step(
            0,
            obs,
            make_observation(sequence=1, position_ned=(105.0, -190.0, 48.0)),
            succeeded=True,
        )
        self.assertFalse(task.update(step_erroneous_offset).done)

    def test_11_invalid_parameters_and_task_type_are_rejected(self) -> None:
        init_obs = make_observation()

        # 1. Wrong task_type
        spec_wrong_type = make_spec(task_type="search_target")
        with self.assertRaises(ContractValidationError):
            DirectPointAgent().reset(spec_wrong_type, init_obs)
        with self.assertRaises(ContractValidationError):
            ReachPointTask().reset(spec_wrong_type, init_obs)

        # 2. Missing target_position_ned
        spec_missing_target = make_spec(include_target=False)
        with self.assertRaises(ContractValidationError):
            DirectPointAgent().reset(spec_missing_target, init_obs)
        with self.assertRaises(ContractValidationError):
            ReachPointTask().reset(spec_missing_target, init_obs)

        # 3. Invalid tolerance_m values
        invalid_tolerances = [0, -1.0, math.nan, math.inf, -math.inf, True, False, "0.5"]
        for tol in invalid_tolerances:
            with self.subTest(invalid_tolerance=tol):
                with self.assertRaises(ContractValidationError):
                    spec_bad_tol = make_spec(tolerance_m=tol)
                    DirectPointAgent().reset(spec_bad_tol, init_obs)
                with self.assertRaises(ContractValidationError):
                    spec_bad_tol = make_spec(tolerance_m=tol)
                    ReachPointTask().reset(spec_bad_tol, init_obs)

        # 4. Invalid action_deadline_s values for Agent
        invalid_deadlines = [0, -5.0, math.nan, math.inf, -math.inf, True, False, "5.0"]
        for dl in invalid_deadlines:
            with self.subTest(invalid_deadline=dl):
                with self.assertRaises(ContractValidationError):
                    spec_bad_dl = make_spec(action_deadline_s=dl)
                    DirectPointAgent().reset(spec_bad_dl, init_obs)

    def test_12_reset_clears_previous_episode_state(self) -> None:
        spec_ep1 = make_spec(
            task_id="ep1",
            home_ned=(0.0, 0.0, 0.0),
            target_ned=(10.0, 0.0, 0.0),
            tolerance_m=0.5,
        )
        obs_ep1 = make_observation(vehicle_id="drone-1", position_ned=(0.0, 0.0, 0.0))

        agent = DirectPointAgent()
        agent.reset(spec_ep1, obs_ep1)
        a1 = agent.act(obs_ep1)
        a2 = agent.act(obs_ep1)
        self.assertEqual(a2.action_id, "direct-point-2")

        # Reset for episode 2 with different vehicle and target
        spec_ep2 = make_spec(
            task_id="ep2",
            home_ned=(50.0, 50.0, 0.0),
            target_ned=(100.0, 100.0, -10.0),
            tolerance_m=1.0,
        )
        obs_ep2 = make_observation(vehicle_id="drone-alpha", position_ned=(50.0, 50.0, 0.0))
        agent.reset(spec_ep2, obs_ep2)

        # Action count must restart at 1, vehicle must be drone-alpha
        a_ep2 = agent.act(obs_ep2)
        self.assertEqual(a_ep2.action_id, "direct-point-1")
        self.assertEqual(a_ep2.vehicle_id, "drone-alpha")
        self.assertEqual(a_ep2.target_position_ned, PositionNed(100.0, 100.0, -10.0))

        # Task reset test
        task = ReachPointTask()
        task.reset(spec_ep1, obs_ep1)
        step_ep1 = make_step(
            0,
            obs_ep1,
            make_observation(vehicle_id="drone-1", position_ned=(10.0, 0.0, 0.0)),
        )
        self.assertTrue(task.update(step_ep1).success)

        task.reset(spec_ep2, obs_ep2)
        # Position from ep1 (10, 0, 0) should not be considered success for ep2 target (100, 100, -10)
        step_ep2_not_reached = make_step(
            0,
            obs_ep2,
            make_observation(vehicle_id="drone-alpha", position_ned=(10.0, 0.0, 0.0)),
        )
        self.assertFalse(task.update(step_ep2_not_reached).done)

    def test_13_close_resets_state_and_requires_reset(self) -> None:
        spec = make_spec()
        obs = make_observation()

        agent = DirectPointAgent()
        agent.reset(spec, obs)
        agent.close()
        with self.assertRaises(RuntimeError):
            agent.act(obs)

        task = ReachPointTask()
        task.reset(spec, obs)
        task.close()
        step = make_step(0, obs, make_observation(sequence=1))
        with self.assertRaises(RuntimeError):
            task.update(step)

    def test_14_example_config_json_is_valid_and_consumable(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "configs" / "reach_point.example.json"
        self.assertTrue(config_path.exists(), f"{config_path} must exist")

        data = json.loads(config_path.read_text(encoding="utf-8"))
        backend_cfg = BackendConfig.from_dict(data["backend_config"])
        task_spec = TaskSpec.from_dict(data["task_spec"])

        self.assertEqual(backend_cfg.backend_type, "mock")
        self.assertEqual(task_spec.task_type, "reach_point")
        self.assertNotEqual(task_spec.home_position_ned, PositionNed(0.0, 0.0, 0.0))

        # Verify that DirectPointAgent and ReachPointTask consume it without error
        init_obs = make_observation(
            vehicle_id=backend_cfg.vehicle_id,
            position_ned=(
                task_spec.home_position_ned.north_m,
                task_spec.home_position_ned.east_m,
                task_spec.home_position_ned.down_m,
            ),
        )

        agent = DirectPointAgent()
        agent.reset(task_spec, init_obs)
        action = agent.act(init_obs)
        self.assertEqual(action.kind, ActionKind.MOVE_TO)

        task = ReachPointTask()
        task.reset(task_spec, init_obs)

    def test_15_full_mock_backend_interaction_loop(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "configs" / "reach_point.example.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        backend_cfg = BackendConfig.from_dict(data["backend_config"])
        task_spec = TaskSpec.from_dict(data["task_spec"])

        backend = MockBackend()
        agent = DirectPointAgent()
        task = ReachPointTask()

        # 1. Reset backend, agent, and task
        obs_0 = backend.reset(backend_cfg, task_spec)
        agent.reset(task_spec, obs_0)
        task.reset(task_spec, obs_0)

        # 2. Step 0: Agent decides MOVE_TO
        act_0 = agent.act(obs_0)
        self.assertEqual(act_0.kind, ActionKind.MOVE_TO)
        exec_0 = backend.execute(act_0)
        self.assertTrue(exec_0.succeeded)
        obs_1 = backend.observe()
        step_0 = StepRecord(0, obs_0, act_0, exec_0, obs_1)

        # 3. Task updates: MockBackend placed drone directly at target position
        progress_0 = task.update(step_0)
        self.assertTrue(progress_0.done)
        self.assertTrue(progress_0.success)
        self.assertEqual(progress_0.reason, TerminationReason.SUCCESS)

        # 4. Step 1: Agent now observes drone at target, should command HOVER
        act_1 = agent.act(obs_1)
        self.assertEqual(act_1.kind, ActionKind.HOVER)
        self.assertIsNone(act_1.target_position_ned)

        # 5. Cleanup and close
        self.assertTrue(backend.cleanup().succeeded)
        agent.close()
        task.close()
        backend.close()

    def test_16_task_rejects_mismatched_vehicle_in_step(self) -> None:
        spec = make_spec()
        task = ReachPointTask()
        init_obs = make_observation(vehicle_id="drone-1")
        task.reset(spec, init_obs)

        obs_wrong_vehicle = make_observation(vehicle_id="drone-2")
        step = make_step(0, obs_wrong_vehicle, obs_wrong_vehicle)
        with self.assertRaises(ContractValidationError):
            task.update(step)

    def test_17_agent_failed_reset_clears_state_and_cannot_act(self) -> None:
        spec_valid = make_spec(target_ned=(10.0, 20.0, -5.0))
        obs_valid = make_observation(vehicle_id="drone-1")
        agent = DirectPointAgent()
        agent.reset(spec_valid, obs_valid)
        self.assertEqual(agent.act(obs_valid).kind, ActionKind.MOVE_TO)

        # Failed reset with invalid tolerance
        spec_invalid = make_spec(target_ned=(99.0, 0.0, 0.0), tolerance_m=-1.0)
        with self.assertRaises(ContractValidationError):
            agent.reset(spec_invalid, obs_valid)

        # Must be in uninitialized state, not retaining old or new target/vehicle
        self.assertIsNone(agent._vehicle_id)
        self.assertIsNone(agent._target_position_ned)
        self.assertEqual(agent._action_count, 0)
        with self.assertRaises(RuntimeError):
            agent.act(obs_valid)

    def test_18_task_failed_reset_clears_state_and_cannot_update(self) -> None:
        spec_valid = make_spec(target_ned=(10.0, 20.0, -5.0))
        obs_valid = make_observation(vehicle_id="drone-1")
        task = ReachPointTask()
        task.reset(spec_valid, obs_valid)

        # Failed reset with invalid tolerance
        spec_invalid = make_spec(target_ned=(99.0, 0.0, 0.0), tolerance_m=-1.0)
        with self.assertRaises(ContractValidationError):
            task.reset(spec_invalid, obs_valid)

        # Must be in uninitialized state
        self.assertIsNone(task._vehicle_id)
        self.assertIsNone(task._target_position_ned)
        step = make_step(0, obs_valid, make_observation(sequence=1, vehicle_id="drone-1"))
        with self.assertRaises(RuntimeError):
            task.update(step)

    def test_19_task_rejects_step_when_action_vehicle_differs_from_reset(self) -> None:
        target = (10.0, 20.0, -5.0)
        spec = make_spec(target_ned=target)
        task = ReachPointTask()
        obs_before = make_observation(sequence=0, vehicle_id="drone-1", position_ned=(0.0, 0.0, 0.0))
        task.reset(spec, obs_before)

        # Post observation reached target with drone-1, but action was issued for drone-2
        obs_after = make_observation(sequence=1, vehicle_id="drone-1", position_ned=target)
        action_wrong_vehicle = Action(
            action_id="act-drone-2",
            kind=ActionKind.MOVE_TO,
            vehicle_id="drone-2",
            deadline_s=5.0,
            target_position_ned=PositionNed(target[0], target[1], target[2]),
        )
        step = make_step(0, obs_before, obs_after, succeeded=True, action=action_wrong_vehicle)

        with self.assertRaises(ContractValidationError):
            task.update(step)


