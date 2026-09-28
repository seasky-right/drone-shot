"""FakeRpc coverage for the v0.2 AirSim plugin execution path."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backends.airsim import AirSimBackend
from builtin_pack.airsim_v02 import AirSimBackendV02, MoveToAgent
from core.plugin_cli import _preflight, run_multi
from core.plugins import PluginRegistry, PluginRegistryError
from core.store import EpisodeStore
from simulator_contract.legacy_airsim import AirSimLegacyAdapter
from tests.test_airsim_backend import FakeRpc


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "builtin_pack" / "drone_plugin.json"
SAMPLE = ROOT / "builtin_pack" / "sample-airsim-v02.json"


class ImageRpc(FakeRpc):
    def call(self, method, *params, timeout):
        if method == "simGetImages":
            if self.image_payloads is not None and not self.image_payloads:
                return [{"width": 1, "height": 1, "image_data_uint8": b"",
                         "image_data_float": [], "time_stamp": 456}]
            payload = self.image_payloads.pop(0) if self.image_payloads is not None else b"pngdata"
            self.calls.append((method, params, timeout))
            return [{"width": 1, "height": 1, "image_data_uint8": payload,
                     "image_data_float": [1.0], "time_stamp": 456}]
        if method == "getMultirotorState" and getattr(self, "_staged_reads", None) is not None and not self.flying:
            if self._staged_reads:
                self.position[2] = -0.24
            self._staged_reads += 1
        result = super().call(method, *params, timeout=timeout)
        if method == "simSetVehiclePose":
            self._staged_reads = 0
        return result


class AirSimV02PluginTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(SAMPLE.read_text(encoding="utf-8"))
        self.registry = PluginRegistry.discover(manifest_paths=(MANIFEST,))
        self.rpc = ImageRpc()

    def run_fake(self, root: Path):
        def factory():
            return AirSimBackend(
                lambda **options: AirSimLegacyAdapter(
                    rpc_factory=lambda host, port: self.rpc, **options))
        with patch("builtin_pack.airsim_v02.AirSimBackend", side_effect=factory):
            return run_multi(self.data, root, registry=self.registry,
                             enable_backend=True)

    def test_full_episode_records_rgb_depth_and_lands(self):
        self.data["required_sensor_resources"] = {"rgb:0": "drone/rgb", "depth:0": "drone/depth"}
        self.assertTrue(_preflight(self.registry, self.data, enable_backend=True))
        with TemporaryDirectory() as directory:
            result = self.run_fake(Path(directory))
            self.assertTrue(result["success"])
            self.assertEqual(EpisodeStore.read_result(directory, result["episode_id"]), result)
            episode = Path(directory) / result["episode_id"]
            before = json.loads((episode / "trajectory.jsonl").read_text(encoding="utf-8"))
            sensors = before["before"]["observations"]["Drone"]["sensors"]
            self.assertEqual({item["kind"] for item in sensors}, {"drone/rgb", "drone/depth"})
            for item in sensors:
                self.assertTrue((episode / item["relative_path"]).is_file())
            self.assertIn("land", [call[0] for call in self.rpc.calls])
            self.assertIn("armDisarm", [call[0] for call in self.rpc.calls])
            self.assertTrue(self.rpc.closed)
            initial = json.loads((episode / "metadata" / "capabilities.json").read_text(encoding="utf-8"))
            confirmed = json.loads((episode / "metadata" / "capabilities-confirmed.json").read_text(encoding="utf-8"))
            self.assertEqual(initial["sensor_resources"], {})
            self.assertEqual(confirmed["sensor_resources"], self.data["required_sensor_resources"])

    def test_unsupported_sensor_kind_fails_static_preflight(self):
        self.data["required_sensor_resources"] = {"thermal:0": "drone/thermal"}
        with self.assertRaises(PluginRegistryError) as caught:
            _preflight(self.registry, self.data, enable_backend=True)
        self.assertEqual(caught.exception.issues[0].details["field"], "sensor_types")

    def test_unknown_resource_fails_after_reset_before_agent_action(self):
        self.data["required_sensor_resources"] = {"rgb:1": "drone/rgb"}
        self.assertTrue(_preflight(self.registry, self.data, enable_backend=True))
        with TemporaryDirectory() as directory:
            with patch.object(MoveToAgent, "act", side_effect=AssertionError("agent acted")):
                with self.assertRaisesRegex(ValueError, "required sensor resource unavailable after reset"):
                    self.run_fake(Path(directory))
            episode = Path(directory) / self.data["episode_id"]
            self.assertTrue((episode / "INCOMPLETE").is_file())
            self.assertIn("sensor_resources.confirm", (episode / "events.jsonl").read_text(encoding="utf-8"))
            self.assertIn("land", [call[0] for call in self.rpc.calls])
            self.assertTrue(self.rpc.closed)

    def test_unconfirmed_resource_fails_before_agent_action_and_cleans_up(self):
        self.data["required_sensor_resources"] = {"rgb:0": "drone/rgb"}
        original_reset = AirSimBackendV02.reset
        for confirmed in ({}, {"rgb:0": "drone/depth"}):
            with self.subTest(confirmed=confirmed), TemporaryDirectory() as directory:
                self.rpc = ImageRpc()
                def reset_with_changed_confirmation(backend, scenario, vehicle_ids):
                    snapshot = original_reset(backend, scenario, vehicle_ids)
                    backend._confirmed_resources = confirmed
                    return snapshot
                with patch.object(AirSimBackendV02, "reset", reset_with_changed_confirmation), \
                     patch.object(MoveToAgent, "act", side_effect=AssertionError("agent acted")):
                    with self.assertRaisesRegex(ValueError, "required sensor resource unavailable after reset"):
                        self.run_fake(Path(directory))
                episode = Path(directory) / self.data["episode_id"]
                self.assertTrue((episode / "INCOMPLETE").is_file())
                self.assertIn("sensor_resources.confirm", (episode / "events.jsonl").read_text(encoding="utf-8"))
                self.assertIn("land", [call[0] for call in self.rpc.calls])
                self.assertTrue(self.rpc.closed)

    def test_failed_move_still_lands_and_records_failure(self):
        self.rpc.fail_method = "moveByVelocityBodyFrame"
        with TemporaryDirectory() as directory:
            result = self.run_fake(Path(directory))
            self.assertEqual(result["status"], "partial_failure")
            self.assertFalse(result["success"])
            self.assertEqual(result["cleanup_errors"], [])
            self.assertIn("land", [call[0] for call in self.rpc.calls])
            self.assertTrue(self.rpc.closed)

    def test_missing_camera_fails_reset_and_releases_control(self):
        self.rpc.image_payloads = [b""]
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "sensor unavailable"):
                self.run_fake(Path(directory))
            self.assertIn("land", [call[0] for call in self.rpc.calls])
            self.assertTrue(self.rpc.closed)

    def test_preflight_requires_enable_and_enforces_single_vehicle(self):
        with self.assertRaises(PluginRegistryError):
            _preflight(self.registry, self.data)
        doubled = deepcopy(self.data)
        doubled["vehicles"].append("drone-2")
        with self.assertRaises(PluginRegistryError):
            _preflight(self.registry, doubled, enable_backend=True)

    def test_invalid_resource_confirmation_declaration_is_rejected(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        entry = next(item for item in manifest["components"]
                     if item["id"] == "drone.v02.airsim/backend")
        entry["capabilities"]["sensor_resource_confirmation"] = "eventually"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "drone_plugin.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            registry = PluginRegistry.discover(manifest_paths=(path,))
            self.assertTrue(any(issue.code == "invalid_capability" for issue in registry.issues))


if __name__ == "__main__":
    unittest.main()
