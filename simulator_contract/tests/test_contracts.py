from __future__ import annotations

import unittest
import struct

from simulator_contract.contracts import (
    SCHEMA_VERSION,
    Capability,
    ErrorCode,
    SensorKind,
    SensorRequest,
    SimulatorContractException,
    ensure_capabilities,
)
from simulator_contract.legacy_airsim import AirSimLegacyAdapter


class FakeRpc:
    def __init__(self, include_simulator_time: bool = True) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False
        self.include_simulator_time = include_simulator_time

    def close(self) -> None:
        self.closed = True

    def call(self, method: str, *params: object, timeout: float) -> object:
        self.calls.append((method, params))
        if method == "ping":
            return True
        if method == "getMultirotorState":
            response = {
                "landed_state": 0,
                "kinematics_estimated": {
                    "position": {"x_val": 1, "y_val": 2, "z_val": -3},
                    "orientation": {"w_val": 1, "x_val": 0, "y_val": 0, "z_val": 0},
                    "linear_velocity": {"x_val": 4, "y_val": 5, "z_val": 6},
                },
            }
            if self.include_simulator_time:
                response["timestamp"] = 123
            return response
        if method == "simGetImages":
            request = params[0][0]
            if request["pixels_as_float"]:
                image = {"width": 2, "height": 1, "image_data_float": [1.0, 2.0]}
            else:
                image = {"width": 2, "height": 1, "image_data_uint8": b"ok"}
            if self.include_simulator_time:
                image["time_stamp"] = 456
            return [image]
        return True


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rpc = FakeRpc()
        self.adapter = AirSimLegacyAdapter(rpc_factory=lambda _host, _port: self.rpc)
        self.assertTrue(self.adapter.connect().accepted)

    def tearDown(self) -> None:
        self.adapter.close()

    def test_state_is_versioned_ned_and_identified(self) -> None:
        state = self.adapter.state("")
        self.assertEqual(state.schema, SCHEMA_VERSION)
        self.assertEqual(state.simulator_time_ns, 123)
        self.assertIsInstance(state.wall_time_ns, int)
        self.assertEqual(state.pose_ned.north_m, 1.0)
        self.assertEqual(state.pose_ned.east_m, 2.0)
        self.assertEqual(state.pose_ned.down_m, -3.0)
        self.assertTrue(state.landed)

    def test_missing_simulator_time_stays_none(self) -> None:
        adapter = AirSimLegacyAdapter(
            rpc_factory=lambda _host, _port: FakeRpc(include_simulator_time=False)
        )
        self.assertTrue(adapter.connect().accepted)
        self.assertIsNone(adapter.state("Drone").simulator_time_ns)
        observation = adapter.observe("Drone", SensorRequest("0", SensorKind.RGB))
        self.assertIsNone(observation.metadata.simulator_time_ns)
        adapter.close()

    def test_observation_keeps_binary_payload_out_of_metadata(self) -> None:
        observation = self.adapter.observe("Drone", SensorRequest("0", SensorKind.RGB))
        self.assertEqual(observation.metadata.schema, SCHEMA_VERSION)
        self.assertEqual(observation.metadata.vehicle_id, "Drone")
        self.assertEqual(observation.payload, b"ok")
        self.assertEqual(observation.metadata.frame_id, "airsim/Drone/camera/0/optical")
        self.assertEqual(observation.metadata.attributes["pose_reference_frame"], "ned")

    def test_depth_observation_is_little_endian_float32_bytes(self) -> None:
        observation = self.adapter.observe("Drone", SensorRequest("0", SensorKind.DEPTH))
        self.assertEqual(observation.metadata.encoding, "airsim-float32")
        self.assertEqual(struct.unpack("<2f", observation.payload), (1.0, 2.0))

    def test_command_maps_body_velocity_and_vehicle_id(self) -> None:
        result = self.adapter.move_body_velocity("Drone", 1.0, 2.0, 3.0, 0.5)
        self.assertTrue(result.accepted)
        self.assertIsNone(result.simulator_time_ns)
        self.assertIsInstance(result.wall_time_ns, int)
        method, params = self.rpc.calls[-1]
        self.assertEqual(method, "moveByVelocityBodyFrame")
        self.assertEqual(params[:4], (1.0, 2.0, 3.0, 0.5))
        self.assertEqual(params[-1], "Drone")

    def test_missing_capability_is_structured(self) -> None:
        error = ensure_capabilities([Capability.STATE], [Capability.STATE, Capability.POINT_CLOUD])
        self.assertIsNotNone(error)
        self.assertEqual(error.code, ErrorCode.NOT_SUPPORTED)
        self.assertEqual(error.details["missing"], "point_cloud")

    def test_sensor_capability_and_resource_discovery_are_separate(self) -> None:
        self.assertIn(Capability.RGB_CAMERA, self.adapter.declared_capabilities)
        self.assertIn(Capability.RGB_CAMERA, self.adapter.capabilities("Drone"))
        self.assertEqual(self.adapter.sensors("Drone"), ())
        self.adapter.observe("Drone", SensorRequest("0", SensorKind.RGB, include_payload=False))
        self.assertEqual(self.adapter.sensors("Drone")[0].sensor_id, "0")

    def test_command_ids_are_unique_while_operation_is_stable(self) -> None:
        first = self.adapter.hover("Drone")
        second = self.adapter.hover("Drone")
        self.assertEqual(first.operation, "hover")
        self.assertEqual(second.operation, "hover")
        self.assertNotEqual(first.command_id, second.command_id)

    def test_unconnected_read_has_structured_error(self) -> None:
        adapter = AirSimLegacyAdapter(rpc_factory=lambda _host, _port: FakeRpc())
        with self.assertRaises(SimulatorContractException) as caught:
            adapter.state("Drone")
        self.assertEqual(caught.exception.error.code, ErrorCode.NOT_CONNECTED)


if __name__ == "__main__":
    unittest.main()
