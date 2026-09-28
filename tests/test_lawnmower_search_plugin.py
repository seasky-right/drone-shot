"""Comprehensive test suite for LawnmowerSearchAgentV02 plugin under drone platform v0.2."""

from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

# ---------------------------------------------------------------------------
# Dynamic environment and source path setup before plugin imports:
# Supports running standalone from either drone-shot root or dev workspace
# without requiring pre-set PYTHONPATH or pre-installed packages.
# ---------------------------------------------------------------------------
_CURRENT_FILE = Path(__file__).resolve()
ROOT = _CURRENT_FILE.parents[1]

# 1. Plugin source directory
_PLUGIN_SRC = ROOT / "plugins" / "lawnmower_search_plugin" / "src"
if _PLUGIN_SRC.exists() and str(_PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SRC))

# 2. Reference repository resolution for spatial task & world packs
_candidate_repos: list[Path] = []
if os.environ.get("DRONE_SHOT_ROOT"):
    _candidate_repos.append(Path(os.environ["DRONE_SHOT_ROOT"]).resolve())
_candidate_repos.append(ROOT)
_candidate_repos.append(Path.cwd().resolve())
try:
    _candidate_repos.append((_CURRENT_FILE.parents[3] / "drone-shot").resolve())
except IndexError:
    pass

_spatial_repo: Path | None = None
for _cand in _candidate_repos:
    if (
        (_cand / "examples" / "spatial_task_pack" / "src").exists()
        and (_cand / "examples" / "spatial_world_pack" / "src").exists()
    ):
        _spatial_repo = _cand
        break

if _spatial_repo is not None:
    _task_src = _spatial_repo / "examples" / "spatial_task_pack" / "src"
    _world_src = _spatial_repo / "examples" / "spatial_world_pack" / "src"
    if _task_src.exists() and str(_task_src) not in sys.path:
        sys.path.insert(0, str(_task_src))
    if _world_src.exists() and str(_world_src) not in sys.path:
        sys.path.insert(0, str(_world_src))
    if str(_spatial_repo) not in sys.path:
        sys.path.insert(0, str(_spatial_repo))

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.data_v02 import (
    ActionChannel,
    ActionV02,
    ContractValidationError,
    EpisodeSnapshotV02,
    PlatformObservationV02,
)
from core.conformance import validate_component_cases
from core.plugin_cli import run_multi
from core.plugins import PluginRegistry, _parse_manifest
from core.store import EpisodeStore
from drone_spatial_search import (
    ACTION_KIND,
    ACTION_SCHEMA,
    REPORT_KIND,
    REPORT_SCHEMA,
    LawnmowerSearchAgentV02,
    create_agent,
    _generate_axis_coordinates,
    _segment_clear_with_obstacles,
    _generate_lawnmower_route,
)
from drone_spatial_tasks.search import (
    SearchAgent,
    SearchTargetEvaluator,
    SearchTargetTask,
)
from drone_spatial_world import SearchGenerator, SearchMockBackend, _world


PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugins" / "lawnmower_search_plugin"
MANIFEST_PATH = PLUGIN_DIR / "src" / "drone_spatial_search" / "drone_plugin.json"
CASES_PATH = PLUGIN_DIR / "component-cases.json"


def _resolve_pack_manifest(package_name: str, fallback_relative: str) -> Path:
    env_root = os.environ.get("DRONE_SHOT_ROOT")
    if env_root and (Path(env_root) / fallback_relative).exists():
        return Path(env_root) / fallback_relative
    if _spatial_repo is not None and (_spatial_repo / fallback_relative).exists():
        return _spatial_repo / fallback_relative
    try:
        spec = importlib.util.find_spec(package_name)
        if spec and spec.origin:
            manifest = Path(spec.origin).parent / "drone_plugin.json"
            if manifest.exists():
                return manifest
    except Exception:
        pass
    if (ROOT / fallback_relative).exists():
        return ROOT / fallback_relative
    try:
        candidate = _CURRENT_FILE.parents[3] / "drone-shot" / fallback_relative
        if candidate.exists():
            return candidate
    except IndexError:
        pass
    raise FileNotFoundError(f"Cannot resolve manifest for {package_name} ({fallback_relative})")


SPATIAL_WORLD_MANIFEST = _resolve_pack_manifest(
    "drone_spatial_world", "examples/spatial_world_pack/src/drone_spatial_world/drone_plugin.json"
)
SPATIAL_TASKS_MANIFEST = _resolve_pack_manifest(
    "drone_spatial_tasks", "examples/spatial_task_pack/src/drone_spatial_tasks/drone_plugin.json"
)


def _make_obs(
    sequence: int = 0,
    vehicle_id: str = "A",
    north_m: float = 0.0,
    east_m: float = 0.0,
    down_m: float = -2.0,
    search_area: dict | None = None,
    detections: list | None = None,
    reports: list | None = None,
    missing_sensors: dict | None = None,
) -> PlatformObservationV02:
    state = {
        "north_m": north_m,
        "east_m": east_m,
        "down_m": down_m,
        "search_area": search_area or {"north": [0.0, 6.0], "east": [0.0, 6.0], "down": [-4.0, -1.0]},
        "detections": detections or [],
        "reports": reports or [],
    }
    return PlatformObservationV02(
        sequence, vehicle_id, 1000 + sequence, state, missing_sensors=missing_sensors or {}
    )


class LawnmowerSearchPluginTests(unittest.TestCase):
    """Formal verification of LawnmowerSearchAgentV02 plugin."""

    def test_01_plugin_manifest_discovery_and_descriptor(self):
        """Manifest can be parsed by Core and registers stable descriptor."""
        self.assertTrue(MANIFEST_PATH.exists(), f"Manifest missing at {MANIFEST_PATH}")
        raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

        pack_id, version, descriptors = _parse_manifest(
            raw, str(MANIFEST_PATH), advertised_id=None, advertised_entry=None
        )
        self.assertEqual(pack_id, "drone.agent.spatial_search")
        self.assertEqual(version, "0.1.0")
        self.assertEqual(len(descriptors), 1)

        desc = descriptors[0]
        self.assertEqual(desc.id, "drone.agent.spatial_search/agent")
        self.assertEqual(desc.type, "agent")
        self.assertEqual(desc.plugin_api, "drone.plugin.api/v0.2")
        self.assertEqual(desc.entry_point, "drone_spatial_search:create_agent")
        self.assertIn("spatial/move-to", desc.capabilities["action_kinds"])
        self.assertIn("spatial/report-target", desc.capabilities["action_kinds"])
        self.assertEqual(desc.requires["coordinate_frame"], "local_ned")
        self.assertEqual(desc.requires["scenario_id"], "sample.spatial-search")

    def test_02_conformance_probe_executes_agent_hook(self):
        """Plugin passes validate_component_cases with truth isolation and valid action output."""
        registry = PluginRegistry.discover(manifest_paths=(MANIFEST_PATH,))
        self.assertTrue(CASES_PATH.exists(), f"Cases path missing: {CASES_PATH}")
        cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))

        report = validate_component_cases(registry, cases)
        self.assertIn("drone.agent.spatial_search/agent", report)
        agent_result = report["drone.agent.spatial_search/agent"]
        self.assertEqual(agent_result["status"], "passed", str(agent_result))
        self.assertIn("hooks", agent_result.get("checks", []))
        self.assertIn("actions", agent_result.get("checks", []))
        self.assertIn("truth_isolation", agent_result.get("checks", []))
        self.assertIn("declared_action_kinds", agent_result.get("checks", []))

    def test_03_factory_and_parameter_validation(self):
        """Factory initializes agent correctly and rejects illegal parameters."""
        agent = create_agent(
            config={
                "lane_spacing_m": 2.5,
                "min_confidence": 0.6,
                "max_report_attempts": 4,
                "waypoint_tolerance_m": 0.4,
                "action_deadline_s": 2.0,
                "flight_down_m": -2.0,
            }
        )
        self.assertEqual(agent._lane_spacing_m, 2.5)
        self.assertEqual(agent._min_confidence, 0.6)
        self.assertEqual(agent._max_report_attempts, 4)
        self.assertEqual(agent._waypoint_tolerance_m, 0.4)
        self.assertEqual(agent._action_deadline_s, 2.0)
        self.assertEqual(agent._flight_down_m, -2.0)

        # Illegal spacing
        with self.assertRaises(ContractValidationError):
            create_agent(config={"lane_spacing_m": -1.0})
        with self.assertRaises(ContractValidationError):
            create_agent(config={"lane_spacing_m": 0.0})
        with self.assertRaises(ContractValidationError):
            create_agent(config={"lane_spacing_m": float("nan")})

        # Illegal confidence
        with self.assertRaises(ContractValidationError):
            create_agent(config={"min_confidence": -0.1})
        with self.assertRaises(ContractValidationError):
            create_agent(config={"min_confidence": 1.1})

        # Illegal report attempts
        with self.assertRaises(ContractValidationError):
            create_agent(config={"max_report_attempts": 0})
        with self.assertRaises(ContractValidationError):
            create_agent(config={"max_report_attempts": -2})

        # Illegal tolerances
        with self.assertRaises(ContractValidationError):
            create_agent(config={"waypoint_tolerance_m": 0.0})
        with self.assertRaises(ContractValidationError):
            create_agent(config={"action_deadline_s": -2.0})

    def test_04_dual_mode_act_hooks_and_single_vehicle_enforcement(self):
        """Agent supports both PlatformObservationV02 and single-vehicle snapshot; rejects multi-vehicle."""
        agent = create_agent()
        obs = _make_obs(sequence=0, north_m=0.0, east_m=0.0)

        # Single vehicle observation
        action = agent.act(obs)
        self.assertIsInstance(action, ActionV02)
        self.assertEqual(action.vehicle_id, "A")
        self.assertEqual(action.kind, ACTION_KIND)

        # Central snapshot with 1 vehicle
        snap1 = EpisodeSnapshotV02(1, {"A": _make_obs(sequence=1)})
        action1 = agent.act(snap1)
        self.assertIsInstance(action1, ActionV02)

        # Multi-vehicle snapshot (2 vehicles) must be explicitly rejected
        snap2 = EpisodeSnapshotV02(
            2,
            {
                "A": _make_obs(sequence=2, vehicle_id="A"),
                "B": _make_obs(sequence=2, vehicle_id="B"),
            },
        )
        with self.assertRaises(ContractValidationError) as ctx:
            agent.act(snap2)
        self.assertIn("single-vehicle", str(ctx.exception).lower())

    def test_05_seed_19_normal_initial_detection_and_immediate_report(self):
        """Seed 19: drone starts near target; emits single accurate report immediately."""
        backend = SearchMockBackend()
        generator = SearchGenerator()
        task = SearchTargetTask(tolerance_m=0.75)
        evaluator = SearchTargetEvaluator(tolerance_m=0.75, missing_penalty_m=100.0)

        scen = generator.generate(seed=19)
        snap = backend.reset(scen.spec, ("A",))
        agent = create_agent(config={"lane_spacing_m": 2.0})

        obs = snap.observations["A"]
        self.assertTrue(len(obs.state["detections"]) > 0, "Seed 19 must have initial detections")

        action = agent.act(obs)
        self.assertEqual(action.kind, REPORT_KIND)
        self.assertEqual(action.channel, ActionChannel.REPORT)
        self.assertEqual(action.payload_schema, REPORT_SCHEMA)
        self.assertEqual(action.payload["target_id"], "T1")

        outcome = backend.handle_report(action)
        self.assertTrue(outcome.succeeded)

        final_snap = backend.observe()
        self.assertTrue(task.complete(final_snap, scen.truth))

        trajectory = [{
            "actions": [action.to_dict()],
            "outcomes": [vars(outcome)],
            "before": snap.to_dict(),
        }]
        result = {
            "episode_id": "seed-19-test",
            "status": "success",
            "truth": scen.truth,
            "trajectory": trajectory,
            "final_snapshot": final_snap.to_dict(),
        }
        metrics = evaluator.evaluate(result)
        self.assertEqual(metrics["spatial/search_success_ratio"], 1.0)
        self.assertEqual(metrics["spatial/search_report_count"], 1.0)
        self.assertEqual(metrics["spatial/search_matched_count"], 1.0)
        self.assertEqual(metrics["spatial/search_missed_target_count"], 0.0)
        self.assertEqual(metrics["spatial/search_false_positive_count"], 0.0)
        self.assertLessEqual(metrics["spatial/search_localization_error_m"], 0.75)
        self.assertEqual(metrics["spatial/search_initial_no_detection_ratio"], 0.0)
        self.assertEqual(metrics["spatial/search_initial_sensor_missing_ratio"], 0.0)

    def test_06_seed_07_autonomous_boustrophedon_navigation_finds_target(self):
        """Seed 7: initial no detection; agent follows lawnmower route, finds target and reports."""
        backend = SearchMockBackend()
        generator = SearchGenerator()
        task = SearchTargetTask(tolerance_m=0.75)
        evaluator = SearchTargetEvaluator(tolerance_m=0.75, missing_penalty_m=100.0)

        scen = generator.generate(seed=7)
        snap = backend.reset(scen.spec, ("A",))
        agent = create_agent(config={"lane_spacing_m": 2.0, "waypoint_tolerance_m": 0.3})

        initial_obs = snap.observations["A"]
        self.assertEqual(len(initial_obs.state["detections"]), 0, "Seed 7 initial detections must be empty")

        trajectory = []
        success = False
        steps_executed = 0

        for step in range(12):
            steps_executed += 1
            curr_snap = backend.observe()
            obs = curr_snap.observations["A"]
            action = agent.act(obs)

            if action.channel is ActionChannel.REPORT:
                outcome = backend.handle_report(action)
            else:
                outcome = backend.execute(action)

            self.assertTrue(outcome.succeeded, f"Action at step {step} failed: {outcome.error}")
            trajectory.append({
                "actions": [action.to_dict()],
                "outcomes": [vars(outcome)],
                "before": curr_snap.to_dict(),
            })

            post_snap = backend.observe()
            if task.complete(post_snap, scen.truth):
                success = True
                break

        self.assertTrue(success, "Lawnmower agent failed to find target within 12 steps")
        self.assertLessEqual(steps_executed, 8, "Expected search to detect target within 8 steps")

        result = {
            "episode_id": "seed-07-test",
            "status": "success",
            "truth": scen.truth,
            "trajectory": trajectory,
            "final_snapshot": backend.observe().to_dict(),
        }
        metrics = evaluator.evaluate(result)
        self.assertEqual(metrics["spatial/search_success_ratio"], 1.0)
        self.assertEqual(metrics["spatial/search_report_count"], 1.0)
        self.assertEqual(metrics["spatial/search_matched_count"], 1.0)
        self.assertEqual(metrics["spatial/search_false_positive_count"], 0.0)
        self.assertEqual(metrics["spatial/search_initial_no_detection_ratio"], 1.0)
        self.assertEqual(metrics["spatial/search_initial_sensor_missing_ratio"], 0.0)

    def test_07_seed_11_sensor_missing_full_sweep_without_false_reports(self):
        """Seed 11: detector missing; agent sweeps coverage area without emitting bogus reports."""
        backend = SearchMockBackend()
        generator = SearchGenerator()
        evaluator = SearchTargetEvaluator(tolerance_m=0.75, missing_penalty_m=100.0)

        scen = generator.generate(seed=11)
        snap = backend.reset(scen.spec, ("A",))
        agent = create_agent(config={"lane_spacing_m": 2.0})

        obs = snap.observations["A"]
        self.assertIn("search-detector", obs.missing_sensors)

        trajectory = []
        for step in range(12):
            curr_snap = backend.observe()
            action = agent.act(curr_snap.observations["A"])
            # In missing detector mode, agent should NEVER emit a report
            self.assertEqual(action.kind, ACTION_KIND)
            outcome = backend.execute(action)
            self.assertTrue(outcome.succeeded)
            trajectory.append({
                "actions": [action.to_dict()],
                "outcomes": [vars(outcome)],
                "before": curr_snap.to_dict(),
            })

        self.assertEqual(len(agent.reported_targets), 0)
        result = {
            "episode_id": "seed-11-test",
            "status": "step_limit",
            "truth": scen.truth,
            "trajectory": trajectory,
            "final_snapshot": backend.observe().to_dict(),
        }
        metrics = evaluator.evaluate(result)
        self.assertEqual(metrics["spatial/search_success_ratio"], 0.0)
        self.assertEqual(metrics["spatial/search_report_count"], 0.0)
        self.assertEqual(metrics["spatial/search_matched_count"], 0.0)
        self.assertEqual(metrics["spatial/search_missed_target_count"], 1.0)
        self.assertEqual(metrics["spatial/search_false_positive_count"], 0.0)
        self.assertEqual(metrics["spatial/search_initial_sensor_missing_ratio"], 1.0)

    def test_08_duplicate_report_suppression_and_bounded_retry(self):
        """Agent suppresses duplicate report when echoed, and retries boundedly when un-echoed."""
        agent = create_agent(config={"max_report_attempts": 3})
        detection = {"target_id": "T1", "north_m": 1.0, "east_m": 2.0, "down_m": -2.0, "confidence": 0.9}

        # Step 0: Detection triggers report attempt 1
        obs0 = _make_obs(sequence=0, detections=[detection], reports=[])
        action0 = agent.act(obs0)
        self.assertEqual(action0.kind, REPORT_KIND)
        self.assertEqual(action0.payload["target_id"], "T1")
        self.assertEqual(agent._report_attempts["T1"], 1)

        # Step 1: Report was NOT echoed / rejected (reports still empty); agent retries (attempt 2)
        obs1 = _make_obs(sequence=1, detections=[detection], reports=[])
        action1 = agent.act(obs1)
        self.assertEqual(action1.kind, REPORT_KIND)
        self.assertEqual(action1.payload["target_id"], "T1")
        self.assertEqual(agent._report_attempts["T1"], 2)

        # Step 2: Now the report is accepted and echoed in platform reports; agent MUST suppress duplicate report!
        obs2 = _make_obs(sequence=2, detections=[detection], reports=[action0.payload])
        action2 = agent.act(obs2)
        self.assertEqual(action2.kind, ACTION_KIND, "Agent must switch to move/hold rather than repeating report")

        # Step 3: Bounded retry exhaustion test with a fresh target T2
        detection2 = {"target_id": "T2", "north_m": 4.0, "east_m": 5.0, "down_m": -2.0, "confidence": 0.85}
        agent_exhaust = create_agent(config={"max_report_attempts": 2})
        obs_t2 = _make_obs(sequence=0, detections=[detection2], reports=[])
        # Attempt 1
        act1 = agent_exhaust.act(obs_t2)
        self.assertEqual(act1.kind, REPORT_KIND)
        # Attempt 2
        act2 = agent_exhaust.act(obs_t2)
        self.assertEqual(act2.kind, REPORT_KIND)
        # Attempt 3: attempts reached max (2), report still not echoed -> stops reporting, commands move/hold
        act3 = agent_exhaust.act(obs_t2)
        self.assertEqual(act3.kind, ACTION_KIND)

    def test_09_episode_reset_clears_internal_state(self):
        """Calling reset clears reported targets, waypoints, and index."""
        agent = create_agent(config={"lane_spacing_m": 2.0})
        obs = _make_obs(detections=[{"target_id": "T1", "north_m": 1.0, "east_m": 2.0, "down_m": -2.0, "confidence": 0.9}])
        agent.act(obs)
        self.assertTrue(len(agent.reported_targets) > 0)

        agent.reset()
        self.assertEqual(len(agent.reported_targets), 0)
        self.assertEqual(len(agent.route), 0)
        self.assertEqual(agent.waypoint_index, 0)
        self.assertFalse(agent.is_closed)

    def test_10_obstacle_collision_and_public_observation_gap_demonstration(self):
        """Demonstrate that naive spacing (lane_spacing_m=3.0) crosses obstacle (3,3) and fails backend execution.

        This test provides reproducible evidence that the public observation contract
        omits obstacle coordinates (present only in world.json assets), causing blind
        lawnmower planners to attempt unfeasible obstacle-intersecting segments.
        """
        world, _ = _world()
        obstacles = world["public"]["obstacles"]
        self.assertTrue(any(obs["center_north_m"] == 3 and obs["center_east_m"] == 3 for obs in obstacles))

        backend = SearchMockBackend()
        generator = SearchGenerator()
        scen = generator.generate(seed=7)
        backend.reset(scen.spec, ("A",))

        # With naive lane spacing = 3.0, lanes are [0.0, 3.0, 6.0].
        # The segment along east=3.0 from north=0.0 to north=6.0 crosses obstacle (3.0, 3.0).
        blocked_move = ActionV02(
            "test-blocked-move", "A", ACTION_KIND, ActionChannel.CONTROL,
            ACTION_SCHEMA, {"north_m": 6.0, "east_m": 3.0, "down_m": -2.0}, 1.0
        )
        outcome = backend.execute(blocked_move)
        self.assertFalse(outcome.succeeded, "Direct move across (3, 3) must be rejected by backend")
        self.assertIn("movement crosses obstacle or world bounds", outcome.error)

    def test_11_safe_spacing_and_obstacle_clearance_verification(self):
        """Verify that lane_spacing_m=2.0 generates only obstacle-clear segments for the Mock grid."""
        world, _ = _world()
        obstacles = world["public"]["obstacles"]

        route = _generate_lawnmower_route(
            min_north=0.0, max_north=6.0,
            min_east=0.0, max_east=6.0,
            down=-2.0, curr_north=0.0, curr_east=3.0,
            lane_spacing_m=2.0,
        )
        # Verify all consecutive segments in route avoid obstacle at (3, 3)
        for i in range(len(route) - 1):
            clear = _segment_clear_with_obstacles(route[i], route[i + 1], obstacles)
            self.assertTrue(clear, f"Route segment {route[i]} -> {route[i+1]} intersects obstacle!")

    def test_12_truth_isolation_and_invalid_state_rejection(self):
        """Agent rejects observation containing truth or invalid coordinates."""
        agent = create_agent()

        # Truth leakage
        obs_leak = _make_obs()
        obs_leak.state["truth"] = {"target": [1, 2, 3]}
        with self.assertRaises(ContractValidationError) as ctx:
            agent.act(obs_leak)
        self.assertIn("truth", str(ctx.exception).lower())

        # Missing coordinate
        obs_missing = _make_obs()
        del obs_missing.state["north_m"]
        with self.assertRaises(ContractValidationError):
            agent.act(obs_missing)

        # Boolean coordinate (must not be defaulted to 0.0)
        obs_bool = _make_obs()
        obs_bool.state["north_m"] = True
        with self.assertRaises(ContractValidationError):
            agent.act(obs_bool)

        # Closed agent rejects act
        agent.close()
        self.assertTrue(agent.is_closed)
        with self.assertRaises(RuntimeError):
            agent.act(_make_obs())

    def test_13_spatial_search_run_multi_end_to_end_and_store_reread(self):
        """Full execution via run_multi with result persistence and re-read."""
        config_path = PLUGIN_DIR / "mixed-search-run.json"
        self.assertTrue(config_path.exists())
        config = json.loads(config_path.read_text(encoding="utf-8"))

        registry = PluginRegistry.discover(
            manifest_paths=(SPATIAL_WORLD_MANIFEST, SPATIAL_TASKS_MANIFEST, MANIFEST_PATH)
        )

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            result = run_multi(config, tmp_path, registry=registry)

            self.assertEqual(result["status"], "success")
            self.assertTrue(result["success"])
            self.assertEqual(result["metrics"]["spatial/search_success_ratio"], 1.0)
            self.assertEqual(result["metrics"]["spatial/search_false_positive_count"], 0.0)

            # Re-read from store
            reloaded = EpisodeStore.read_result(tmp_path, result["episode_id"])
            self.assertEqual(reloaded["status"], "success")
            self.assertEqual(reloaded["metrics"], result["metrics"])

    def test_14_benchmark_comparison_with_sample_search_agent(self):
        """Compare LawnmowerSearchAgentV02 vs baseline SearchAgent across seeds 7, 11, 19."""
        backend = SearchMockBackend()
        generator = SearchGenerator()
        task = SearchTargetTask(tolerance_m=0.75)
        evaluator = SearchTargetEvaluator(tolerance_m=0.75, missing_penalty_m=100.0)

        for seed in (7, 11, 19):
            scen = generator.generate(seed)

            # Run baseline SearchAgent
            backend.reset(scen.spec, ("A",))
            base_agent = SearchAgent(offset_report=False)
            base_success = False
            base_traj = []
            for _ in range(12):
                snap = backend.observe()
                act = base_agent.act(snap.observations["A"])
                out = backend.handle_report(act) if act.channel is ActionChannel.REPORT else backend.execute(act)
                base_traj.append({"actions": [act.to_dict()], "outcomes": [vars(out)], "before": snap.to_dict()})
                if task.complete(backend.observe(), scen.truth):
                    base_success = True
                    break
            base_metrics = evaluator.evaluate({
                "episode_id": f"base-{seed}", "status": "success" if base_success else "step_limit",
                "truth": scen.truth, "trajectory": base_traj, "final_snapshot": backend.observe().to_dict(),
            })

            # Run LawnmowerSearchAgentV02
            backend.reset(scen.spec, ("A",))
            lawn_agent = create_agent(config={"lane_spacing_m": 2.0})
            lawn_success = False
            lawn_traj = []
            for _ in range(12):
                snap = backend.observe()
                act = lawn_agent.act(snap.observations["A"])
                out = backend.handle_report(act) if act.channel is ActionChannel.REPORT else backend.execute(act)
                lawn_traj.append({"actions": [act.to_dict()], "outcomes": [vars(out)], "before": snap.to_dict()})
                if task.complete(backend.observe(), scen.truth):
                    lawn_success = True
                    break
            lawn_metrics = evaluator.evaluate({
                "episode_id": f"lawn-{seed}", "status": "success" if lawn_success else "step_limit",
                "truth": scen.truth, "trajectory": lawn_traj, "final_snapshot": backend.observe().to_dict(),
            })

            # Both should achieve consistent benchmark classification
            self.assertEqual(
                lawn_metrics["spatial/search_success_ratio"],
                base_metrics["spatial/search_success_ratio"],
                f"Mismatch on seed {seed}"
            )
            self.assertEqual(lawn_metrics["spatial/search_false_positive_count"], 0.0)

    def test_15_configured_obstacle_collision_rejected_before_emitting_action(self):
        """When an obstacle is configured, agent actively checks route segments and rejects dangerous moves."""
        obstacle = {"center_north_m": 3.0, "center_east_m": 3.0, "radius_m": 0.75}

        # Case 1: Agent with lane_spacing_m=3.0 would plan route crossing (3.0, 3.0).
        # With obstacle configured, agent MUST reject before emitting dangerous action.
        agent = create_agent(config={"lane_spacing_m": 3.0, "obstacles": [obstacle]})
        obs = _make_obs(north_m=0.0, east_m=3.0, down_m=-2.0)

        with self.assertRaises(ContractValidationError) as ctx:
            agent.act(obs)
        self.assertIn("obstacle", str(ctx.exception).lower())

        # Case 2: Obstacle blocking initial move from current position to first waypoint
        obs_blocked_init = {"center_north_m": 0.0, "center_east_m": 1.0, "radius_m": 0.5}
        agent_init_blocked = create_agent(config={"lane_spacing_m": 2.0, "obstacles": [obs_blocked_init]})
        # Drone starts at (0.0, 2.0) and route first waypoint is at (0.0, 0.0) -> crosses (0.0, 1.0)
        obs_start = _make_obs(north_m=0.0, east_m=2.0, down_m=-2.0)
        with self.assertRaises(ContractValidationError) as ctx:
            agent_init_blocked.act(obs_start)
        self.assertIn("obstacle", str(ctx.exception).lower())

    def test_16_invalid_or_missing_search_area_raises_contract_validation_error(self):
        """Agent strictly validates search_area; never silently degrades to in-place hover."""
        agent = create_agent()

        # Missing search_area
        obs_no_area = _make_obs()
        del obs_no_area.state["search_area"]
        with self.assertRaises(ContractValidationError) as ctx:
            agent.act(obs_no_area)
        self.assertIn("search_area", str(ctx.exception).lower())

        # Inverted bounds min >= max
        obs_inv = _make_obs(search_area={"north": [6.0, 0.0], "east": [0.0, 6.0], "down": [-4.0, -1.0]})
        with self.assertRaises(ContractValidationError) as ctx:
            agent.act(obs_inv)
        self.assertIn("bound", str(ctx.exception).lower())

        # Non-finite bound
        obs_nan = _make_obs()
        obs_nan.state["search_area"] = {"north": [0.0, float("nan")], "east": [0.0, 6.0], "down": [-4.0, -1.0]}
        with self.assertRaises(ContractValidationError) as ctx:
            agent.act(obs_nan)
        self.assertIn("finite", str(ctx.exception).lower())

        # Missing axis (e.g. down)
        obs_no_down = _make_obs(search_area={"north": [0.0, 6.0], "east": [0.0, 6.0]})
        with self.assertRaises(ContractValidationError) as ctx:
            agent.act(obs_no_down)
        self.assertIn("axis", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
