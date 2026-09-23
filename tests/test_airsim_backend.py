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
        self.yaw = math.pi / 2
        self.freeze_motion = False

    def close(self):
        self.closed = True

    def call(self, method, *params, timeout):
        self.calls.append((method, params, timeout))
        if method == self.fail_method:
            return False
        if method == "reset":
            self.position = [0.0, 0.0, 0.0]
            self.flying = False
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
                    "linear_velocity": {"x_val": 0, "y_val": 0, "z_val": 0},
                },
            }
        if method == "simGetImages":
            return [{"width": 1, "height": 1, "image_data_uint8": b"pngdata", "time_stamp": 456}]
        return True


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
    def test_cleanup_land_failure_remains_separate(self):
        self.backend.reset(self.config(), self.task)
        self.rpc.fail_method = "land"
        cleanup = self.backend.cleanup()
        self.assertTrue(cleanup.attempted)
        self.assertFalse(cleanup.succeeded)
        self.assertEqual(cleanup.error.code, "simulator")
    def test_missing_sensor_has_reason(self):
        self.backend.reset(BackendConfig("airsim", "drone-1", {"sensors": [{"sensor_id": "0", "kind": "rgb"}]}), self.task)
        self.assertEqual(self.backend.observe().missing_sensors["rgb:0"], "resource_root_not_configured")


if __name__ == "__main__":
    unittest.main()
