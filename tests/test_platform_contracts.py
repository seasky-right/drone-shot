from __future__ import annotations

import json
import math
import unittest

from contracts import Action, ActionKind, CleanupResult, ContractValidationError, EpisodeResult, ExecutionResult, PlatformObservation, PositionNed, SensorReference, TaskSpec, TerminationReason, time_budget_exhausted


class PlatformContractTests(unittest.TestCase):
    def test_task_spec_json_round_trip_and_nonzero_home(self) -> None:
        source = {"schema": "drone.platform.contract/v0.1", "task_id": "reach-1", "task_type": "reach_point", "time_budget_s": 10.0, "home_position_ned": {"north_m": 100.0, "east_m": -20.0, "down_m": 8.0}, "altitude_reference": "relative_to_home", "parameters": {"target": "p1"}, "initial_state": {}, "seed": 3}
        spec = TaskSpec.from_dict(json.loads(json.dumps(source)))
        self.assertEqual(spec.home_position_ned.north_m, 100.0)
        self.assertEqual(spec.to_dict(), source)

    def test_unknown_fields_and_unknown_enum_are_rejected(self) -> None:
        with self.assertRaises(ContractValidationError):
            TaskSpec.from_dict({"schema": "drone.platform.contract/v0.1", "task_id": "x", "task_type": "y", "time_budget_s": 1, "home_position_ned": {"north_m": 0, "east_m": 0, "down_m": 0}, "altitude_reference": "relative_to_home", "surprise": True})
        with self.assertRaises(ContractValidationError):
            Action.from_dict({"schema": "drone.platform.contract/v0.1", "action_id": "a", "kind": "fly_anywhere", "vehicle_id": "v", "deadline_s": 1, "target_position_ned": None})

    def test_nonfinite_values_binary_json_and_invalid_time_are_rejected(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(ContractValidationError):
                    PositionNed(value, 0, 0)
        with self.assertRaises(ContractValidationError):
            TaskSpec("x", "y", 1, PositionNed(0, 0, 0), "relative_to_home", {"raw": b"binary"})
        with self.assertRaises(ContractValidationError):
            PlatformObservation(0, "v", PositionNed(0, 0, 0), (0, 0, 0), -1)

    def test_json_observation_sensor_reference_and_execution_round_trip(self) -> None:
        sensor = SensorReference("camera-0", "rgb", "images/0001.png", 1)
        observation = PlatformObservation(0, "v", PositionNed(0, 0, 0), (0, 0, 0), 2, sensors=(sensor,))
        self.assertEqual(PlatformObservation.from_dict(json.loads(json.dumps(observation.to_dict()))), observation)
        result = ExecutionResult("a", True, True, True, 3)
        self.assertEqual(ExecutionResult.from_dict(json.loads(json.dumps(result.to_dict()))), result)
        with self.assertRaises(ContractValidationError):
            SensorReference("camera-0", "rgb", "C:\\machine\\image.png", 1)

    def test_episode_result_enum_json_round_trip_and_unknown_observation_field(self) -> None:
        observation = PlatformObservation(0, "v", PositionNed(0, 0, 0), (0, 0, 0), 2)
        result = EpisodeResult("e", "t", TerminationReason.TIMEOUT, False, observation, CleanupResult(True, True), {"elapsed_s": 1.0})
        self.assertEqual(EpisodeResult.from_dict(json.loads(json.dumps(result.to_dict()))), result)
        payload = observation.to_dict(); payload["unexpected"] = 1
        with self.assertRaises(ContractValidationError):
            PlatformObservation.from_dict(payload)
        for invalid in (1, 0, "true"):
            with self.subTest(success=invalid):
                with self.assertRaises(ContractValidationError):
                    EpisodeResult("e", "t", TerminationReason.TIMEOUT, invalid, observation, CleanupResult(True, True))
        for invalid_metrics in ({"ok": True}, {"": 1.0}, ["not-an-object"]):
            with self.subTest(metrics=invalid_metrics):
                with self.assertRaises(ContractValidationError):
                    EpisodeResult("e", "t", TerminationReason.TIMEOUT, False, observation, CleanupResult(True, True), invalid_metrics)

    def test_action_requires_target_only_for_move_to(self) -> None:
        with self.assertRaises(ContractValidationError):
            Action("a", ActionKind.MOVE_TO, "v", 1, None)
        with self.assertRaises(ContractValidationError):
            Action("a", ActionKind.HOVER, "v", 1, PositionNed(0, 0, 0))

    def test_artifact_paths_reject_parent_traversal_and_windows_separators(self) -> None:
        observation = PlatformObservation(0, "v", PositionNed(0, 0, 0), (0, 0, 0), 2)
        for path in ("../outside.png", "a/../../outside.png", "a\\b.png"):
            with self.subTest(sensor_path=path):
                with self.assertRaises(ContractValidationError):
                    SensorReference("camera", "rgb", path, 1)
            with self.subTest(record_path=path):
                with self.assertRaises(ContractValidationError):
                    EpisodeResult("e", "t", TerminationReason.TIMEOUT, False, observation, CleanupResult(True, True), record_path=path)

    def test_time_budget_uses_monotonic_elapsed_seconds(self) -> None:
        task = TaskSpec("t", "x", 2.0, PositionNed(0, 0, 0), "relative_to_home")
        self.assertFalse(time_budget_exhausted(task, 1.999))
        self.assertTrue(time_budget_exhausted(task, 2.0))
