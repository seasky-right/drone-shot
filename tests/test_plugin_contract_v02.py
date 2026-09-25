from __future__ import annotations

import json
import math
import ast
import unittest
from pathlib import Path

from contracts.data_v02 import (
    ActionChannel, ActionV02, AgentBindingV02, CapabilitySetV02,
    EpisodeSnapshotV02, PlatformObservationV02, ScenarioSpecV02,
    SensorReferenceV02,
)
from contracts.migration_v02 import migrate_action_v01, migrate_observation_v01
from contracts.model import ContractValidationError
from contracts.plugin_v02 import PLUGIN_API_VERSION, PluginType


# Valid and invalid wire fixtures are kept together so schema tests are readable.
ACTION = {"schema": "drone.platform.contract/v0.2", "action_id": "a1",
          "vehicle_id": "uav-1", "kind": "drone.control/move_to", "channel": "control",
          "payload_schema": "drone.action.move_to/v0.2",
          "payload": {"target_position_ned": {"north_m": 1, "east_m": 2, "down_m": -3}},
          "deadline_s": 2.0, "correlation_id": None}
OBSERVATION = {"schema": "drone.platform.contract/v0.2", "sequence": 0,
               "vehicle_id": "uav-1", "wall_time_ns": 100, "simulator_time_ns": None,
               "state": {"position_ned": {"north_m": 0, "east_m": 0, "down_m": 0}},
               "sensors": [], "missing_sensors": {"front": "not installed"},
               "extensions": {"example.extra/confidence": 0.8}}


class PluginContractV02Tests(unittest.TestCase):
    def test_wire_fixtures(self) -> None:
        root = Path(__file__).resolve().parents[1] / "fixtures" / "plugin_v02"
        self.assertEqual(ActionV02.from_dict(json.loads((root / "valid_action.json").read_text(encoding="utf-8"))).action_id, "move-1")
        self.assertEqual(ScenarioSpecV02.from_dict(json.loads((root / "valid_scenario.json").read_text(encoding="utf-8"))).scenario_id, "urban-1")
        with self.assertRaises(ContractValidationError):
            ActionV02.from_dict(json.loads((root / "invalid_action_unknown_field.json").read_text(encoding="utf-8")))
        with self.assertRaises(ContractValidationError):
            ScenarioSpecV02.from_dict(json.loads((root / "invalid_scenario_path.json").read_text(encoding="utf-8")))

    def test_new_contract_and_core_boundary_imports(self) -> None:
        root = Path(__file__).resolve().parents[1]
        paths = list((root / "contracts").glob("*.py")) + [root / "core" / "multi_vehicle.py"]
        forbidden = ("agents", "tasks", "evaluators", "backends", "airsim", "simulator_contract")
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level == 0]
            imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
            for imported in imports:
                with self.subTest(path=path.name, imported=imported):
                    self.assertFalse(imported.startswith(forbidden), f"{path} imports concrete component {imported}")

    def test_plugin_identity_and_component_categories(self) -> None:
        self.assertEqual(PLUGIN_API_VERSION, "drone.plugin.api/v0.2")
        self.assertEqual({value.value for value in PluginType}, {
            "backend", "task", "agent", "evaluator", "scenario", "scenario_generator",
            "runtime_provider", "training_driver", "result_processor", "benchmark"})

    def test_action_round_trip_and_invalid_wire_rejection(self) -> None:
        action = ActionV02.from_dict(json.loads(json.dumps(ACTION)))
        self.assertEqual(action.channel, ActionChannel.CONTROL)
        self.assertEqual(action.to_dict(), ACTION)
        for change in ({"schema": "drone.platform.contract/v0.1"}, {"kind": "move_to"},
                       {"channel": "teleport"}, {"deadline_s": math.inf},
                       {"payload": {"binary": b"x"}}, {"payload": {1: "bad"}},
                       {"surprise": 1}):
            with self.subTest(change=change), self.assertRaises(ContractValidationError):
                ActionV02.from_dict({**ACTION, **change})

    def test_multi_vehicle_snapshot_and_agent_binding(self) -> None:
        first = PlatformObservationV02.from_dict(OBSERVATION)
        second = PlatformObservationV02.from_dict({**OBSERVATION, "vehicle_id": "uav-2"})
        snapshot = EpisodeSnapshotV02(0, {"uav-1": first, "uav-2": second})
        self.assertEqual(EpisodeSnapshotV02.from_dict(snapshot.to_dict()), snapshot)
        self.assertEqual(AgentBindingV02.from_dict({"agent_id": "central", "vehicle_ids": ["uav-1", "uav-2"]}).vehicle_ids,
                         ("uav-1", "uav-2"))
        with self.assertRaises(ContractValidationError):
            EpisodeSnapshotV02(0, {"wrong": first})
        with self.assertRaises(ContractValidationError):
            EpisodeSnapshotV02(0, {"uav-1": first}, {"uav-1": "lost"})

    def test_all_vehicles_missing_snapshot_round_trip_requires_reasons(self) -> None:
        missing = {"uav-1": "telemetry unavailable", "uav-2": "link lost"}
        snapshot = EpisodeSnapshotV02(4, {}, missing)
        self.assertEqual(EpisodeSnapshotV02.from_dict(
            json.loads(json.dumps(snapshot.to_dict()))), snapshot)
        for reasons in ({}, {"uav-1": ""}):
            with self.subTest(reasons=reasons), self.assertRaises(ContractValidationError):
                EpisodeSnapshotV02(4, {}, reasons)

    def test_observation_artifact_and_truth_boundaries(self) -> None:
        obs = PlatformObservationV02.from_dict(OBSERVATION)
        self.assertEqual(PlatformObservationV02.from_dict(obs.to_dict()), obs)
        for path in ("../outside", "C:/camera/image.png", "a\\b", "a//b"):
            with self.subTest(path=path), self.assertRaises(ContractValidationError):
                SensorReferenceV02("front", "drone.sensor/rgb", path, 1)
        for change in ({"ground_truth": {"target": 1}},
                       {"extensions": {"unnamespaced": 1}},
                       {"missing_sensors": {"front": ""}}):
            with self.subTest(change=change), self.assertRaises(ContractValidationError):
                PlatformObservationV02.from_dict({**OBSERVATION, **change})

    def test_scenario_and_capability_round_trip(self) -> None:
        scenario = ScenarioSpecV02("urban-1", "1.0", "sha256:abc", {"map": "scene/map.json"},
                                   3, {"uav-1": {"spawn": [0, 0, 0]}})
        self.assertEqual(ScenarioSpecV02.from_dict(scenario.to_dict()), scenario)
        capabilities = CapabilitySetV02(("drone.control/move_to",), ("drone.sensor/rgb",),
                                         {"front": "drone.sensor/rgb"}, "world_ned", ("wall",),
                                         2, ("load", "reset"), False, True)
        self.assertEqual(CapabilitySetV02.from_dict(capabilities.to_dict()), capabilities)
        with self.assertRaises(ContractValidationError):
            CapabilitySetV02.from_dict({**capabilities.to_dict(), "max_vehicles": True})
        with self.assertRaises(ContractValidationError):
            ScenarioSpecV02.from_dict({**scenario.to_dict(), "resources": {"map": "../escape"}})

    def test_explicit_v01_migration(self) -> None:
        old_action = {"schema": "drone.platform.contract/v0.1", "action_id": "a1", "kind": "move_to",
                      "vehicle_id": "uav-1", "deadline_s": 2,
                      "target_position_ned": {"north_m": 1, "east_m": 2, "down_m": -3}}
        converted = migrate_action_v01(old_action)
        self.assertEqual(converted.to_dict()["payload"], ACTION["payload"])
        self.assertEqual(converted.channel, ActionChannel.CONTROL)
        old_observation = {"schema": "drone.platform.contract/v0.1", "sequence": 0,
                           "vehicle_id": "uav-1", "position_ned": {"north_m": 0, "east_m": 0, "down_m": 0},
                           "velocity_ned_mps": [0, 0, 0], "wall_time_ns": 100,
                           "simulator_time_ns": None, "sensors": [{"sensor_id": "front", "kind": "rgb",
                           "relative_path": "images/0.png", "captured_wall_time_ns": 100}], "missing_sensors": {}}
        observation = migrate_observation_v01(old_observation)
        self.assertEqual(observation.sensors[0].kind, "drone.sensor/rgb")
        self.assertEqual(observation.state["position_ned"]["north_m"], 0.0)
        with self.assertRaises(ContractValidationError):
            migrate_observation_v01({**old_observation, "sensors": [{**old_observation["sensors"][0], "kind": "thermal"}]})
        with self.assertRaises(ContractValidationError):
            migrate_action_v01({**old_action, "kind": "land"})


if __name__ == "__main__":
    unittest.main()
