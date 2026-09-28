"""Tests for DirectPointAgent v0.2 candidate plugin.

Verifies:
1. Static plugin discovery and manifest descriptor resolution.
2. Hook execution via validate_component_cases (Conformance probe).
3. Factory instantiation and configuration validation.
4. Both CentralAgent (snapshot) and VehicleAgent (observation) act hooks.
5. Closed-loop multi-step execution with builtin Mock Backend, Scenario, and Task.
6. Execution recording and result re-reading via EpisodeStore.
7. Negative / incompatibility cases:
   - Action requirement mismatch (e.g. drone/hover on MockBackend).
   - Schema validation failure on invalid configuration.
   - Closed agent rejection and invalid input type rejection.
8. Regression isolation of v0.1 agents.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from contracts.data_v02 import (
    ActionChannel,
    ActionV02,
    EpisodeSnapshotV02,
    PlatformObservationV02,
)
from contracts.model import ContractValidationError
from core import plugins
from core.conformance import validate_component_cases
from core.multi_vehicle import PreflightError
from core.plugin_cli import run_multi
from core.plugins import PluginRegistry
from core.store import EpisodeStore


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "direct_agent_plugin"
PLUGIN_SRC = PLUGIN_DIR / "src"
BUILTIN_MANIFEST = ROOT / "builtin_pack" / "drone_plugin.json"
PLUGIN_MANIFEST = PLUGIN_SRC / "drone_direct_agent" / "drone_plugin.json"


class DirectPointAgentPluginTests(unittest.TestCase):
    def setUp(self) -> None:
        import sys
        if str(PLUGIN_SRC) not in sys.path:
            sys.path.insert(0, str(PLUGIN_SRC))

    def _get_registry(self) -> PluginRegistry:
        return PluginRegistry.discover(manifest_paths=(BUILTIN_MANIFEST, PLUGIN_MANIFEST))

    def test_01_plugin_manifest_discovery_and_descriptor(self) -> None:
        """Scenario 1: Plugin is discovered from manifest without importing factory code."""
        registry = self._get_registry()
        self.assertEqual(len(registry.issues), 0, f"Registry issues: {registry.issues}")

        descriptor = registry.resolve("drone.agent.direct/agent", "agent", {})
        self.assertEqual(descriptor.pack_id, "drone.agent.direct")
        self.assertEqual(descriptor.type, "agent")
        self.assertEqual(descriptor.version, "0.1.0")
        self.assertEqual(descriptor.plugin_api, "drone.plugin.api/v0.2")
        self.assertEqual(descriptor.entry_point, "drone_direct_agent:create_agent")
        self.assertIn("drone/move", descriptor.capabilities.get("action_kinds", ()))
        self.assertIn("drone/move", descriptor.requires.get("action_kinds", ()))

    def test_02_conformance_probe_executes_agent_hook(self) -> None:
        """Scenario 2: validate_component_cases executes actual agent hooks and passes."""
        registry = self._get_registry()
        case_path = PLUGIN_DIR / "component-cases.json"
        self.assertTrue(case_path.exists())
        cases = json.loads(case_path.read_text(encoding="utf-8"))

        report = validate_component_cases(registry, cases)
        self.assertIn("drone.agent.direct/agent", report)
        agent_result = report["drone.agent.direct/agent"]
        self.assertEqual(agent_result["status"], "passed", str(agent_result))
        self.assertIn("hooks", agent_result.get("checks", []))
        self.assertIn("actions", agent_result.get("checks", []))
        self.assertIn("truth_isolation", agent_result.get("checks", []))

    def test_03_factory_and_agent_config_validation(self) -> None:
        """Scenario 3: Factory creates agent with validated config; rejects invalid numbers."""
        from drone_direct_agent import DirectPointAgent, create_agent

        # Valid default
        agent = create_agent(config={})
        self.assertEqual(agent.target_north_m, 5.0)
        self.assertIsNone(agent.step_size_m)
        self.assertEqual(agent.tolerance_m, 0.1)

        # Valid custom
        agent2 = create_agent(config={
            "target_north_m": 10.5,
            "step_size_m": 2.0,
            "tolerance_m": 0.25,
            "action_deadline_s": 2.0,
        })
        self.assertEqual(agent2.target_north_m, 10.5)
        self.assertEqual(agent2.step_size_m, 2.0)
        self.assertEqual(agent2.tolerance_m, 0.25)
        self.assertEqual(agent2.action_deadline_s, 2.0)

        # Invalid config rejected by constructor
        with self.assertRaises(ContractValidationError):
            DirectPointAgent(target_north_m=float("nan"))
        with self.assertRaises(ContractValidationError):
            DirectPointAgent(target_north_m=5.0, step_size_m=-1.0)
        with self.assertRaises(ContractValidationError):
            DirectPointAgent(target_north_m=5.0, tolerance_m=-0.1)
        with self.assertRaises(ContractValidationError):
            DirectPointAgent(target_north_m=5.0, action_deadline_s=0.0)

    def test_04_dual_mode_act_hooks(self) -> None:
        """Scenario 4: Agent supports both CentralAgent (snapshot) and VehicleAgent (observation)."""
        from drone_direct_agent import DirectPointAgent

        agent = DirectPointAgent(target_north_m=6.0, step_size_m=2.0)

        obs_a = PlatformObservationV02(
            sequence=0,
            vehicle_id="vehicle-1",
            wall_time_ns=1000,
            state={"north_m": 0.0, "east_m": 0.0, "down_m": 0.0},
        )
        obs_b = PlatformObservationV02(
            sequence=0,
            vehicle_id="vehicle-2",
            wall_time_ns=1000,
            state={"north_m": 3.0, "east_m": 0.0, "down_m": 0.0},
        )

        # Single vehicle mode (VehicleAgent)
        action_single = agent.act(obs_a)
        self.assertIsInstance(action_single, ActionV02)
        self.assertEqual(action_single.vehicle_id, "vehicle-1")
        self.assertEqual(action_single.kind, "drone/move")
        self.assertEqual(action_single.payload["north_m"], 2.0)

        # Central multi-vehicle mode (CentralAgent)
        snapshot = EpisodeSnapshotV02(sequence=0, observations={"vehicle-1": obs_a, "vehicle-2": obs_b})
        actions_central = agent.act(snapshot)
        self.assertEqual(len(actions_central), 2)
        action_map = {act.vehicle_id: act for act in actions_central}
        self.assertEqual(action_map["vehicle-1"].payload["north_m"], 2.0)
        self.assertEqual(action_map["vehicle-2"].payload["north_m"], 5.0)

    def test_05_step_by_step_and_holding_behavior(self) -> None:
        """Scenario 5: Agent steps incrementally towards target and holds once within tolerance."""
        from drone_direct_agent import DirectPointAgent

        agent = DirectPointAgent(target_north_m=5.0, step_size_m=2.0, tolerance_m=0.1)

        # Step 0: pos 0.0 -> commands 2.0
        obs = PlatformObservationV02(0, "A", 100, {"north_m": 0.0})
        act = agent.act(obs)
        self.assertEqual(act.payload["north_m"], 2.0)

        # Step 1: pos 2.0 -> commands 4.0
        obs = PlatformObservationV02(1, "A", 200, {"north_m": 2.0})
        act = agent.act(obs)
        self.assertEqual(act.payload["north_m"], 4.0)

        # Step 2: pos 4.0 -> delta is 1.0 < step_size 2.0 -> commands target 5.0
        obs = PlatformObservationV02(2, "A", 300, {"north_m": 4.0})
        act = agent.act(obs)
        self.assertEqual(act.payload["north_m"], 5.0)

        # Step 3: pos 5.0 -> within tolerance -> holds 5.0
        obs = PlatformObservationV02(3, "A", 400, {"north_m": 5.0})
        act = agent.act(obs)
        self.assertEqual(act.payload["north_m"], 5.0)

    def test_06_mixed_run_with_builtin_mock_and_store_reread(self) -> None:
        """Scenario 6: Mixed run with builtin Mock Backend, Scenario, Task, and re-reading result."""
        registry = self._get_registry()
        config_path = PLUGIN_DIR / "mixed-run.json"
        self.assertTrue(config_path.exists())
        config = json.loads(config_path.read_text(encoding="utf-8"))

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            result = run_multi(config, tmp_path, registry=registry)

            # Verification of episode execution
            self.assertEqual(result["status"], "success")
            self.assertTrue(result["success"])
            self.assertGreater(result["step_count"], 0)

            # Verification of result re-reading via EpisodeStore
            reloaded = EpisodeStore.read_result(tmp_path, result["episode_id"])
            self.assertEqual(reloaded, result)

            # Verification of run record on disk
            run_json = json.loads((tmp_path / result["episode_id"] / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(
                run_json["components"]["agent:direct_agent"]["id"],
                "drone.agent.direct/agent",
            )
            self.assertEqual(
                run_json["components"]["backend"]["id"],
                "drone.v02.mock/backend",
            )

    def test_07_incompatibility_unsupported_action_kind_fails_preflight(self) -> None:
        """Scenario 7: Incompatible run requiring drone/hover fails preflight with clear error."""
        registry = self._get_registry()
        config_path = PLUGIN_DIR / "incompatible-run.json"
        self.assertTrue(config_path.exists())
        config = json.loads(config_path.read_text(encoding="utf-8"))

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            with self.assertRaises((PreflightError, plugins.PluginRegistryError)) as ctx:
                run_multi(config, tmp_path, registry=registry)
            self.assertTrue(
                "unsupported" in str(ctx.exception).lower()
                or "required action" in str(ctx.exception).lower()
            )

    def test_08_invalid_config_schema_rejected_at_preflight(self) -> None:
        """Scenario 8: Config violating JSON schema is rejected during preflight before factory import."""
        registry = self._get_registry()
        config_path = PLUGIN_DIR / "mixed-run.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))

        # Inject invalid config: negative step_size_m
        bad_config = dict(config)
        bad_config["components"] = dict(config["components"])
        bad_config["components"]["agents"] = {
            "direct_agent": {
                "id": "drone.agent.direct/agent",
                "config": {"step_size_m": -5.0},
            }
        }
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            with self.assertRaises(Exception) as ctx:
                run_multi(bad_config, tmp_path, registry=registry)
            # Must mention configuration or schema validation failure
            err_msg = str(ctx.exception).lower()
            self.assertTrue("config" in err_msg or "schema" in err_msg or "greater than 0" in err_msg)

    def test_09_closed_agent_rejects_further_actions(self) -> None:
        """Scenario 9: Closed agent raises RuntimeError if act() is called."""
        from drone_direct_agent import DirectPointAgent

        agent = DirectPointAgent(target_north_m=5.0)
        agent.close()
        self.assertTrue(agent.closed)

        obs = PlatformObservationV02(0, "A", 100, {"north_m": 0.0})
        with self.assertRaises(RuntimeError):
            agent.act(obs)

    def test_10_regression_existing_agents_intact(self) -> None:
        """Scenario 10: Existing v0.1 agents DirectPointAgent, FixedRouteAgent, LawnmowerSearchAgent remain intact."""
        from agents.reach_point import DirectPointAgent as LegacyDirectPointAgent
        from agents.fixed_route import FixedRouteAgent as LegacyFixedRouteAgent
        from agents.lawnmower_search import LawnmowerSearchAgent as LegacyLawnmowerAgent

        dp = LegacyDirectPointAgent()
        self.assertIsNotNone(dp)
        fr = LegacyFixedRouteAgent()
        self.assertIsNotNone(fr)
        lm = LegacyLawnmowerAgent()
        self.assertIsNotNone(lm)

    def test_11_missing_or_invalid_north_m_in_observation_rejected(self) -> None:
        """Scenario 11: Missing or invalid north_m in observation state is rejected and not defaulted to 0."""
        from drone_direct_agent import DirectPointAgent

        agent = DirectPointAgent(target_north_m=5.0)

        # Missing north_m entirely
        obs_missing = PlatformObservationV02(0, "A", 100, {"east_m": 0.0, "down_m": 0.0})
        with self.assertRaises(ContractValidationError) as ctx:
            agent.act(obs_missing)
        self.assertIn("missing required 'north_m'", str(ctx.exception))

        # Boolean north_m
        obs_bool = PlatformObservationV02(0, "A", 100, {"north_m": True})
        with self.assertRaises(ContractValidationError) as ctx:
            agent.act(obs_bool)
        self.assertIn("finite number", str(ctx.exception))

        # String north_m
        obs_str = PlatformObservationV02(0, "A", 100, {"north_m": "zero"})
        with self.assertRaises(ContractValidationError) as ctx:
            agent.act(obs_str)
        self.assertIn("finite number", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

