from __future__ import annotations

import math
from pathlib import Path
import tempfile
import time
import unittest

from backends.airsim import AirSimBackend
from contracts import Action, ActionKind, BackendConfig, PlatformContractException, PositionNed, TaskSpec
from simulator_contract.legacy_airsim import AirSimLegacyAdapter


class FakeRpc:
    def __init__(self) -> None:
        self.calls = []
        self.closed = False
        self.position = [0.0, 0.0, 0.0]
        self.flying = False
        self.fail_method = None
        self.false_after_effect_methods = set()
        self.yaw = math.pi / 2
        self.freeze_motion = False
        self.velocity = [0.0, 0.0, 0.0]
        self.image_payloads = None

    def close(self):
        self.closed = True

    def call(self, method, *params, timeout):
        self.calls.append((method, params, timeout))
        if method == self.fail_method:
            return False
        if method == "reset":
            self.position = [0.0, 0.0, 0.0]
            self.flying = False
        if method == "simSetVehiclePose":
            pose = params[0]["position"]
            self.position = [pose["x_val"], pose["y_val"], pose["z_val"]]
        if method == "takeoff":
            self.position[2] = -1.0
            self.flying = True
        if method == "moveByVelocityBodyFrame" and not self.freeze_motion:
            forward, right, down, duration = params[:4]
            self.position[0] += (math.cos(self.yaw) * forward - math.sin(self.yaw) * right) * duration
            self.position[1] += (math.sin(self.yaw) * forward + math.cos(self.yaw) * right) * duration
            self.position[2] += down * duration
        if method == "land":
            self.position[2] = 0.0
            self.flying = False
        if method == "moveToZ":
            self.position[2] = params[0]
        if method == "getMultirotorState":
            return {
                "timestamp": 123,
                "landed_state": 1 if self.flying else 0,
                "kinematics_estimated": {
                    "position": dict(zip(("x_val", "y_val", "z_val"), self.position)),
                    "orientation": {"w_val": math.cos(self.yaw / 2), "x_val": 0, "y_val": 0, "z_val": math.sin(self.yaw / 2)},
                    "linear_velocity": dict(zip(("x_val", "y_val", "z_val"), self.velocity)),
                },
            }
        if method == "simGetImages":
            payload = self.image_payloads.pop(0) if self.image_payloads is not None else b"pngdata"
            return [{"width": 1, "height": 1, "image_data_uint8": payload, "time_stamp": 456}]
        return False if method in self.false_after_effect_methods else True


class AirSimBackendTests(unittest.TestCase):
    def setUp(self):
        self.rpc = FakeRpc()
        self.backend = AirSimBackend(lambda **kw: AirSimLegacyAdapter(rpc_factory=lambda host, port: self.rpc, **kw))
        self.task = TaskSpec("reach", "reach_point", 20, PositionNed(0, 0, 0), "relative_to_home")
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.backend.close()
        self.temp.cleanup()

    def config(self, **options):
        return BackendConfig("airsim", "drone-1", {"position_tolerance_m": 0.1, **options}, self.temp.name)

    def test_full_lifecycle_and_yaw_translation(self):
        initial = self.backend.reset(self.config(sensors=[{"sensor_id": "0", "kind": "rgb"}]), self.task)
        self.assertEqual(initial.sequence, 0)
        self.assertTrue(initial.sensors)
        self.assertEqual((Path(self.temp.name) / initial.sensors[0].relative_path).read_bytes(), b"pngdata")
        action = Action("move", ActionKind.MOVE_TO, "drone-1", 3, PositionNed(0, 1, -1))
        result = self.backend.execute(action)
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(self.backend.observe().sequence, 1)
        self.assertAlmostEqual(self.backend.observe().position_ned.east_m, 1, delta=0.1)
        move = next(item for item in self.rpc.calls if item[0] == "moveByVelocityBodyFrame")
        self.assertGreater(move[1][0], 0.9)
        self.assertAlmostEqual(move[1][1], 0, delta=0.01)
        self.assertTrue(self.backend.cleanup().succeeded)
        self.backend.close()
        self.backend.close()
        self.assertTrue(self.rpc.closed)

    def test_staging_pose_and_state_confirmed_legacy_false(self):
        self.rpc.false_after_effect_methods = {"takeoff", "hover"}
        self.task = TaskSpec("reach", "reach_point", 20, PositionNed(4, 0, -0.25), "relative_to_home")
        initial = self.backend.reset(self.config(
            spawn_position_ned={"north_m": 4, "east_m": 0, "down_m": -0.25},
            home_tolerance_m=0.1,
        ), self.task)
        self.assertAlmostEqual(initial.position_ned.north_m, 4)
        self.assertIn("takeoff", self.backend.runtime_metadata["state_confirmed_rpc_false"])
        self.assertTrue(self.backend.execute(Action("hover", ActionKind.HOVER, "drone-1", 1)).succeeded)
        self.assertIn("hover", self.backend.runtime_metadata["state_confirmed_rpc_false"])
        self.assertTrue(self.backend.cleanup().succeeded)

    def test_false_takeoff_without_ascent_fails(self):
        self.rpc.fail_method = "takeoff"
        with self.assertRaises(PlatformContractException) as caught:
            self.backend.reset(self.config(takeoff_confirm_timeout_s=0.01,
                                           state_poll_interval_s=0.005), self.task)
        self.assertEqual(caught.exception.error.code, "simulator")

    def test_false_hover_while_moving_fails(self):
        self.backend.reset(self.config(state_poll_interval_s=0.005), self.task)
        self.rpc.false_after_effect_methods = {"hover"}
        self.rpc.velocity[2] = 2.0
        result = self.backend.execute(Action("hover", ActionKind.HOVER, "drone-1", 0.03))
        self.assertFalse(result.succeeded)
        self.assertEqual(result.error.code, "simulator")

    def test_false_rpc_is_structured_failure(self):
        self.rpc.fail_method = "armDisarm"
        with self.assertRaises(PlatformContractException) as caught:
            self.backend.reset(self.config(), self.task)
        self.assertEqual(caught.exception.error.code, "simulator")

    def test_low_level_false_and_unknown_landed_state(self):
        adapter = AirSimLegacyAdapter(rpc_factory=lambda host, port: self.rpc)
        self.assertTrue(adapter.connect().accepted)
        self.rpc.fail_method = "hover"
        failed = adapter.hover("")
        self.assertFalse(failed.accepted)
        self.assertEqual(failed.error.code.value, "simulator")
        self.rpc.fail_method = None
        original = self.rpc.call
        def unknown_landed(method, *params, timeout):
            result = original(method, *params, timeout=timeout)
            if method == "getMultirotorState":
                result.pop("landed_state")
            return result
        self.rpc.call = unknown_landed
        self.assertIsNone(adapter.state("").landed)
        adapter.close()
    def test_unconnected_and_wrong_vehicle(self):
        action = Action("x", ActionKind.HOVER, "drone-1", 1)
        self.assertEqual(self.backend.execute(action).error.code, "not_connected")
        with self.assertRaises(PlatformContractException):
            self.backend.observe()
        self.backend.reset(self.config(), self.task)
        wrong = Action("wrong", ActionKind.HOVER, "other", 1)
        self.assertEqual(self.backend.execute(wrong).error.code, "invalid_argument")

    def test_move_timeout_is_not_success(self):
        self.backend.reset(self.config(move_speed_mps=0.1), self.task)
        self.rpc.freeze_motion = True
        action = Action("far", ActionKind.MOVE_TO, "drone-1", 0.05, PositionNed(10, 0, -1))
        result = self.backend.execute(action)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.error.code, "timeout")

    def test_late_rpc_reply_does_not_count_as_success(self):
        self.backend.reset(self.config(), self.task)
        original = self.rpc.call
        def slow_hover(method, *params, timeout):
            if method == "hover":
                time.sleep(0.02)
            return original(method, *params, timeout=timeout)
        self.rpc.call = slow_hover
        result = self.backend.execute(Action("late", ActionKind.HOVER, "drone-1", 0.005))
        self.assertFalse(result.succeeded)
        self.assertEqual(result.error.code, "timeout")
    def test_hover_waits_for_vertical_speed_to_settle(self):
        self.backend.reset(self.config(state_poll_interval_s=0.005), self.task)
        self.rpc.velocity[2] = 2.0
        result = self.backend.execute(Action("descending", ActionKind.HOVER, "drone-1", 0.03))
        self.assertFalse(result.succeeded)
        self.assertEqual(result.error.code, "timeout")
        self.rpc.velocity[2] = 0.0
        settled = self.backend.execute(Action("settled", ActionKind.HOVER, "drone-1", 0.1))
        self.assertTrue(settled.succeeded, settled.error)

    def test_sensor_files_stay_distinct_across_observations_and_id_collisions(self):
        self.rpc.image_payloads = [b"first", b"second", b"third", b"fourth"]
        sensors = [{"sensor_id": "cam/a", "kind": "rgb"},
                   {"sensor_id": "cam?a", "kind": "rgb"}]
        first = self.backend.reset(self.config(sensors=sensors), self.task)
        second = self.backend.observe()
        self.assertEqual((first.sequence, second.sequence), (0, 0))
        references = (*first.sensors, *second.sensors)
        self.assertEqual(len(references), 4)
        paths = [reference.relative_path for reference in references]
        self.assertEqual(len(set(paths)), 4)
        self.assertEqual([reference.sensor_id for reference in references],
                         ["cam/a", "cam?a", "cam/a", "cam?a"])
        self.assertEqual([(Path(self.temp.name) / path).read_bytes() for path in paths],
                         [b"first", b"second", b"third", b"fourth"])

    def test_cleanup_land_failure_remains_separate(self):
        self.backend.reset(self.config(), self.task)
        self.rpc.fail_method = "land"
        cleanup = self.backend.cleanup()
        self.assertTrue(cleanup.attempted)
        self.assertFalse(cleanup.succeeded)
        self.assertEqual(cleanup.error.code, "simulator")
        self.assertIn(("armDisarm", (False, ""), 5.0), self.rpc.calls)
        self.assertIn(("enableApiControl", (False, ""), 5.0), self.rpc.calls)

    def test_missing_sensor_has_reason(self):
        self.backend.reset(BackendConfig("airsim", "drone-1", {"sensors": [{"sensor_id": "0", "kind": "rgb"}]}), self.task)
        self.assertEqual(self.backend.observe().missing_sensors["rgb:0"], "resource_root_not_configured")


if __name__ == "__main__":
    unittest.main()
