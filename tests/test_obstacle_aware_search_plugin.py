"""Comprehensive test suite for ObstacleAwareSearchAgentV02 plugin under drone platform v0.2."""

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

# 1. Plugin source directory (supports both in-tree and external plugin paths)
_PLUGIN_SRC = ROOT / "plugins" / "obstacle_aware_search_plugin" / "src"
if _PLUGIN_SRC.exists() and str(_PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SRC))

# 2. Existing Lawnmower baseline plugin (for head-to-head comparison)
_LAWNMOWER_SRC = ROOT / "plugins" / "lawnmower_search_plugin" / "src"
if _LAWNMOWER_SRC.exists() and str(_LAWNMOWER_SRC) not in sys.path:
    sys.path.insert(0, str(_LAWNMOWER_SRC))

# 3. Reference repository resolution for spatial task & world packs
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
from core.plugins import PluginRegistry
from core.store import EpisodeStore
from drone_spatial_obstacle_search import (
    ACTION_KIND,
    ACTION_SCHEMA,
    REPORT_KIND,
    REPORT_SCHEMA,
    ObstacleAwareSearchAgentV02,
    create_agent,
    _generate_axis_coordinates,
    _point_clear_with_obstacles,
    _segment_clear_with_obstacles,
    _plan_detour,
    _generate_obstacle_aware_route,
)
from drone_spatial_search import LawnmowerSearchAgentV02
from drone_spatial_tasks.search import (
    SearchAgent,
    SearchTargetEvaluator,
    SearchTargetTask,
)
from drone_spatial_world import SearchGenerator, SearchMockBackend, _world


PLUGIN_DIR = ROOT / "plugins" / "obstacle_aware_search_plugin"
MANIFEST_PATH = PLUGIN_DIR / "src" / "drone_spatial_obstacle_search" / "drone_plugin.json"
CASES_PATH = PLUGIN_DIR / "component-cases.json"
LAWNMOWER_MANIFEST = (
    ROOT / "plugins" / "lawnmower_search_plugin" / "src" / "drone_spatial_search" / "drone_plugin.json"
)


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
    north: float = 0.0,
    east: float = 0.0,
    down: float = -2.0,
    sequence: int = 0,
    vehicle_id: str = "A",
    search_area: dict | None = None,
    detections: list | None = None,
    reports: list | None = None,
    missing: bool = False,
    extra_state: dict | None = None,
) -> PlatformObservationV02:
    state = {
        "north_m": north,
        "east_m": east,
        "down_m": down,
        "search_area": search_area
        if search_area is not None
        else {"north": [0.0, 6.0], "east": [0.0, 6.0], "down": [-4.0, -1.0]},
        "detections": list(detections) if detections is not None else [],
        "reports": list(reports) if reports is not None else [],
    }
    if extra_state:
        state.update(extra_state)
    return PlatformObservationV02(
        sequence=sequence,
        vehicle_id=vehicle_id,
        wall_time_ns=sequence * 1_000_000,
        state=state,
        missing_sensors={"search-detector": "unavailable"} if missing else {},
    )


class ObstacleAwareSearchPluginTests(unittest.TestCase):
    """Test suite for ObstacleAwareSearchAgentV02 plugin."""

    def _get_registry(self) -> PluginRegistry:
        manifests = [MANIFEST_PATH, SPATIAL_WORLD_MANIFEST, SPATIAL_TASKS_MANIFEST]
        if LAWNMOWER_MANIFEST.exists():
            manifests.append(LAWNMOWER_MANIFEST)
        registry = PluginRegistry.discover(manifest_paths=manifests)
        self.assertEqual(len(registry.issues), 0, f"Registry issues: {registry.issues}")
        return registry

    def test_01_plugin_manifest_discovery_and_descriptor(self):
        """Scenario 1: Manifest discovers correctly with drone.agent.spatial_obstacle_search."""
        registry = self._get_registry()
        descriptor = registry.resolve("drone.agent.spatial_obstacle_search/agent", "agent", {})
        self.assertEqual(descriptor.pack_id, "drone.agent.spatial_obstacle_search")
        self.assertEqual(descriptor.type, "agent")
        self.assertEqual(descriptor.version, "0.1.0")
        self.assertEqual(descriptor.plugin_api, "drone.plugin.api/v0.2")
        self.assertEqual(
            descriptor.entry_point, "drone_spatial_obstacle_search:create_agent"
        )
        self.assertIn("spatial/move-to", descriptor.capabilities.get("action_kinds", ()))
        self.assertIn(
            "spatial/report-target", descriptor.capabilities.get("action_kinds", ())
        )

    def test_02_conformance_probe_executes_agent_hook(self):
        """Scenario 2: Conformance probe runs validate_component_cases successfully."""
        registry = self._get_registry()
        self.assertTrue(CASES_PATH.exists())
        cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        report = validate_component_cases(registry, cases)
        self.assertIn("drone.agent.spatial_obstacle_search/agent", report)
        case_result = report["drone.agent.spatial_obstacle_search/agent"]
        self.assertEqual(case_result["status"], "passed", str(case_result))
        self.assertIn("hooks", case_result.get("checks", []))
        self.assertIn("actions", case_result.get("checks", []))

    def test_03_factory_and_parameter_validation(self):
        """Scenario 3: Factory instantiation and strict parameter validation."""
        agent = create_agent(
            config={
                "lane_spacing_m": 2.5,
                "safety_margin_m": 0.3,
                "min_confidence": 0.6,
                "max_report_attempts": 2,
                "obstacles": [{"center_north_m": 3.0, "center_east_m": 3.0, "radius_m": 0.75}],
            }
        )
        self.assertEqual(agent.lane_spacing_m, 2.5)
        self.assertEqual(agent.safety_margin_m, 0.3)
        self.assertEqual(len(agent.obstacles), 1)

        # Negative parameters
        with self.assertRaises(ContractValidationError):
            create_agent(config={"lane_spacing_m": -1.0})
        with self.assertRaises(ContractValidationError):
            create_agent(config={"safety_margin_m": -0.1})
        with self.assertRaises(ContractValidationError):
            create_agent(config={"min_confidence": 1.5})
        with self.assertRaises(ContractValidationError):
            create_agent(config={"max_report_attempts": 0})
        with self.assertRaises(ContractValidationError):
            create_agent(config={"obstacles": [{"center_north_m": 3.0}]})  # missing keys

    def test_04_dual_mode_act_hooks_and_single_vehicle_enforcement(self):
        """Scenario 4: Supports both PlatformObservation and EpisodeSnapshot; rejects multi-vehicle."""
        agent = create_agent()
        obs = _make_obs(north=0.0, east=0.0)

        # Vehicle observation
        act1 = agent.act(obs)
        self.assertIsInstance(act1, ActionV02)

        # Single-vehicle snapshot
        agent.reset()
        snap_single = EpisodeSnapshotV02(0, {"A": obs})
        act2 = agent.act(snap_single)
        self.assertIsInstance(act2, ActionV02)

        # Multi-vehicle snapshot -> must reject
        agent.reset()
        obs_b = _make_obs(vehicle_id="B")
        snap_multi = EpisodeSnapshotV02(0, {"A": obs, "B": obs_b})
        with self.assertRaises(ContractValidationError) as ctx:
            agent.act(snap_multi)
        self.assertIn("single-vehicle", str(ctx.exception).lower())

    def test_05_no_obstacles_normal_sweep_behavior(self):
        """Scenario 5: When no obstacles configured, executes standard boustrophedon sweep."""
        agent = create_agent(config={"lane_spacing_m": 2.0, "obstacles": []})
        obs = _make_obs(north=0.0, east=0.0)
        act = agent.act(obs)
        self.assertEqual(act.kind, ACTION_KIND)
        self.assertAlmostEqual(act.payload["east_m"], 0.0)
        self.assertAlmostEqual(act.payload["north_m"], 6.0)

    def test_06_central_obstacle_detour_and_target_discovery(self):
        """Scenario 6: Seed 7 with central obstacle: detours safely without colliding with backend."""
        gen = SearchGenerator()
        scen = gen.generate(7)
        backend = SearchMockBackend()
        backend.reset(scen.spec, ("A",))
        task = SearchTargetTask(tolerance_m=0.75)
        evaluator = SearchTargetEvaluator(tolerance_m=0.75, missing_penalty_m=100.0)

        # Configure agent with lane_spacing = 3.0 (would pass right through center) and known obstacle
        agent = create_agent(
            config={
                "lane_spacing_m": 3.0,
                "safety_margin_m": 0.25,
                "obstacles": [{"center_north_m": 3.0, "center_east_m": 3.0, "radius_m": 0.75}],
            }
        )

        success = False
        trajectory = []
        collision_rejections = 0

        for step in range(12):
            snap = backend.observe()
            act = agent.act(snap.observations["A"])
            if act.channel is ActionChannel.REPORT:
                out = backend.handle_report(act)
            else:
                out = backend.execute(act)

            if not out.succeeded:
                collision_rejections += 1

            trajectory.append(
                {"actions": [act.to_dict()], "outcomes": [vars(out)], "before": snap.to_dict()}
            )
            if task.complete(backend.observe(), scen.truth):
                success = True
                break

        self.assertTrue(success, "ObstacleAwareSearchAgent failed to complete search task")
        self.assertEqual(collision_rejections, 0, "No movements should be rejected by backend")

        metrics = evaluator.evaluate(
            {
                "episode_id": "detour-test-7",
                "status": "success" if success else "step_limit",
                "truth": scen.truth,
                "trajectory": trajectory,
                "final_snapshot": backend.observe().to_dict(),
            }
        )
        self.assertEqual(metrics["spatial/search_success_ratio"], 1.0)
        self.assertEqual(metrics["spatial/search_false_positive_count"], 0.0)

    def test_07_multiple_obstacles_navigation(self):
        """Scenario 7: Generates safe detour routes avoiding multiple obstacles; validates every segment."""
        obstacles = [
            {"center_north_m": 2.0, "center_east_m": 2.0, "radius_m": 0.5},
            {"center_north_m": 4.0, "center_east_m": 4.0, "radius_m": 0.5},
        ]
        safety_margin = 0.2
        agent = create_agent(
            config={"lane_spacing_m": 2.0, "safety_margin_m": safety_margin, "obstacles": obstacles}
        )
        curr_pos = (0.0, 0.0, -2.0)
        obs = _make_obs(north=curr_pos[0], east=curr_pos[1], down=curr_pos[2])
        act = agent.act(obs)
        self.assertEqual(act.kind, ACTION_KIND)
        self.assertTrue(len(agent.waypoints) > 0)

        # Build full sequence of points starting from current vehicle position
        all_points = [curr_pos] + list(agent.waypoints)

        # Rigorously verify that EVERY segment clears all obstacles by more than radius + safety_margin
        for idx in range(len(all_points) - 1):
            p_start = all_points[idx]
            p_end = all_points[idx + 1]

            # 1. Platform-level segment clearance check including safety margin
            self.assertTrue(
                _segment_clear_with_obstacles(p_start, p_end, obstacles, safety_margin),
                f"Segment {idx} from {p_start} to {p_end} violates obstacle safety margin"
            )

            # 2. Mathematical distance projection verification against each obstacle
            delta_n = p_end[0] - p_start[0]
            delta_e = p_end[1] - p_start[1]
            seg_len_sq = delta_n * delta_n + delta_e * delta_e

            for obs_item in obstacles:
                cn = obs_item["center_north_m"]
                ce = obs_item["center_east_m"]
                req_dist = obs_item["radius_m"] + safety_margin

                if seg_len_sq == 0.0:
                    dist = math.hypot(p_start[0] - cn, p_start[1] - ce)
                else:
                    proj = ((cn - p_start[0]) * delta_n + (ce - p_start[1]) * delta_e) / seg_len_sq
                    frac = max(0.0, min(1.0, proj))
                    near_n = p_start[0] + frac * delta_n
                    near_e = p_start[1] + frac * delta_e
                    dist = math.hypot(near_n - cn, near_e - ce)

                self.assertGreater(
                    dist,
                    req_dist,
                    f"Segment {idx} ({p_start[:2]} -> {p_end[:2]}) min distance {dist:.3f}m "
                    f"is not strictly greater than required {req_dist:.3f}m for obstacle at ({cn}, {ce})"
                )

    def test_08_completely_blocked_fails_explicitly_before_dangerous_action(self):
        """Scenario 8: Completely blocked passage explicitly fails with ContractValidationError."""
        # Giant obstacle spanning across the entire search area
        blocked_obs = [{"center_north_m": 3.0, "center_east_m": 3.0, "radius_m": 4.5}]
        agent = create_agent(config={"lane_spacing_m": 2.0, "obstacles": blocked_obs})
        obs = _make_obs(north=0.0, east=0.0)
        with self.assertRaises(ContractValidationError) as ctx:
            agent.act(obs)
        self.assertIn("block", str(ctx.exception).lower())

    def test_09_invalid_or_missing_search_area_raises_contract_validation_error(self):
        """Scenario 9: Missing or malformed search_area raises ContractValidationError."""
        agent = create_agent()

        # Missing search_area
        obs_no_area = _make_obs()
        del obs_no_area.state["search_area"]
        with self.assertRaises(ContractValidationError) as ctx:
            agent.act(obs_no_area)
        self.assertIn("search_area", str(ctx.exception).lower())

        # Inverted bounds min >= max
        obs_inv = _make_obs(
            search_area={"north": [6.0, 0.0], "east": [0.0, 6.0], "down": [-4.0, -1.0]}
        )
        with self.assertRaises(ContractValidationError) as ctx:
            agent.act(obs_inv)
        self.assertIn("bound", str(ctx.exception).lower())

        # Non-finite bound
        obs_nan = _make_obs()
        obs_nan.state["search_area"] = {
            "north": [0.0, float("nan")],
            "east": [0.0, 6.0],
            "down": [-4.0, -1.0],
        }
        with self.assertRaises(ContractValidationError) as ctx:
            agent.act(obs_nan)
        self.assertIn("finite", str(ctx.exception).lower())

    def test_10_target_detection_and_echo_based_deduplication(self):
        """Scenario 10: Detection triggers report; confirmed report echo strictly suppresses duplicates."""
        agent = create_agent()

        # Step 0: Target detected with confidence 0.9, not yet in reports -> emits report
        obs_det = _make_obs(
            north=0.0,
            east=0.0,
            detections=[
                {"target_id": "T1", "north_m": 3.0, "east_m": 3.0, "down_m": -2.0, "confidence": 0.9}
            ],
            reports=[],
        )
        act = agent.act(obs_det)
        self.assertEqual(act.kind, REPORT_KIND)
        self.assertEqual(act.payload["target_id"], "T1")

        # Step 1: Target still in detections, but platform echoes T1 in reports -> deduplication suppresses report
        obs_confirmed = _make_obs(
            north=0.0,
            east=0.0,
            sequence=1,
            detections=[
                {"target_id": "T1", "north_m": 3.0, "east_m": 3.0, "down_m": -2.0, "confidence": 0.9}
            ],
            reports=[{"target_id": "T1", "north_m": 3.0, "east_m": 3.0, "down_m": -2.0}],
        )
        act2 = agent.act(obs_confirmed)
        self.assertEqual(act2.kind, ACTION_KIND)
        self.assertNotEqual(act2.channel, ActionChannel.REPORT)

    def test_11_un_echoed_report_allows_bounded_retry(self):
        """Scenario 11: Unconfirmed report allows bounded retry up to max_report_attempts."""
        agent = create_agent(config={"max_report_attempts": 3})
        obs_det = _make_obs(
            detections=[
                {"target_id": "T1", "north_m": 3.0, "east_m": 3.0, "down_m": -2.0, "confidence": 0.8}
            ],
            reports=[],
        )

        # Attempt 1
        act1 = agent.act(obs_det)
        self.assertEqual(act1.kind, REPORT_KIND)

        # Attempt 2
        act2 = agent.act(obs_det)
        self.assertEqual(act2.kind, REPORT_KIND)

        # Attempt 3
        act3 = agent.act(obs_det)
        self.assertEqual(act3.kind, REPORT_KIND)

        # Attempt 4: Max attempts reached, suppresses further reporting
        act4 = agent.act(obs_det)
        self.assertEqual(act4.kind, ACTION_KIND)

    def test_12_sensor_missing_continues_sweep_zero_false_positives(self):
        """Scenario 12: Seed 11 missing sensor completes sweep without false reports."""
        gen = SearchGenerator()
        scen = gen.generate(11)
        backend = SearchMockBackend()
        backend.reset(scen.spec, ("A",))
        evaluator = SearchTargetEvaluator(tolerance_m=0.75, missing_penalty_m=100.0)

        agent = create_agent(config={"lane_spacing_m": 2.0})
        trajectory = []
        for step in range(12):
            snap = backend.observe()
            act = agent.act(snap.observations["A"])
            self.assertEqual(act.kind, ACTION_KIND)
            out = backend.execute(act)
            trajectory.append(
                {"actions": [act.to_dict()], "outcomes": [vars(out)], "before": snap.to_dict()}
            )

        metrics = evaluator.evaluate(
            {
                "episode_id": "missing-sensor-11",
                "status": "step_limit",
                "truth": scen.truth,
                "trajectory": trajectory,
                "final_snapshot": backend.observe().to_dict(),
            }
        )
        self.assertEqual(metrics["spatial/search_false_positive_count"], 0.0)
        self.assertEqual(metrics["spatial/search_report_count"], 0.0)

    def test_13_truth_isolation_and_invalid_state_rejection(self):
        """Scenario 13: Agent rejects observations containing truth or invalid coordinates."""
        agent = create_agent()

        # Truth leaked
        obs_leak = _make_obs(extra_state={"truth": {"target": [1, 2, 3]}})
        with self.assertRaises(ContractValidationError) as ctx:
            agent.act(obs_leak)
        self.assertIn("truth", str(ctx.exception).lower())

        # Missing coordinate
        obs_no_north = _make_obs()
        del obs_no_north.state["north_m"]
        with self.assertRaises(ContractValidationError) as ctx:
            agent.act(obs_no_north)
        self.assertIn("north_m", str(ctx.exception).lower())

    def test_14_end_to_end_run_multi_and_store_reread(self):
        """Scenario 14: run_multi end-to-end execution and result re-reading via EpisodeStore."""
        registry = self._get_registry()
        config_path = PLUGIN_DIR / "mixed-search-run.json"
        self.assertTrue(config_path.exists())
        config = json.loads(config_path.read_text(encoding="utf-8"))

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            result = run_multi(config, tmp_path, registry=registry)
            self.assertTrue(result["success"])
            read_back = EpisodeStore.read_result(tmp_path, result["episode_id"])
            self.assertEqual(read_back["metrics"], result["metrics"])
            self.assertEqual(result["metrics"]["spatial/search_success_ratio"], 1.0)
            self.assertEqual(result["metrics"]["spatial/search_false_positive_count"], 0.0)

    def test_15_head_to_head_comparison_with_baseline_lawnmower(self):
        """Scenario 15: Comparative verification where Lawnmower rejects/collides and ObstacleAware succeeds."""
        gen = SearchGenerator()
        scen = gen.generate(7)
        obs_config = [{"center_north_m": 3.0, "center_east_m": 3.0, "radius_m": 0.75}]
        initial_obs = scen.spec.initial_states["A"]
        obs = PlatformObservationV02(0, "A", 1, dict(initial_obs))

        # 1. Baseline Lawnmower Search Agent with lane_spacing=3.0 and configured obstacles
        lawnmower = LawnmowerSearchAgentV02(lane_spacing_m=3.0, obstacles=obs_config)
        lawnmower_rejected = False
        try:
            lawnmower.act(obs)
        except ContractValidationError as e:
            lawnmower_rejected = True
            self.assertIn("crosses configured obstacle", str(e))
        self.assertTrue(
            lawnmower_rejected,
            "Baseline Lawnmower should explicitly reject route crossing the central obstacle",
        )

        # 2. ObstacleAwareSearchAgentV02 with identical configuration
        obstacle_agent = create_agent(
            config={
                "lane_spacing_m": 3.0,
                "safety_margin_m": 0.25,
                "obstacles": obs_config,
            }
        )
        backend = SearchMockBackend()
        backend.reset(scen.spec, ("A",))
        task = SearchTargetTask(tolerance_m=0.75)
        evaluator = SearchTargetEvaluator(tolerance_m=0.75, missing_penalty_m=100.0)

        obs_success = False
        obs_traj = []
        steps = 0
        total_distance = 0.0
        prev_pos = (initial_obs["north_m"], initial_obs["east_m"])

        for step in range(12):
            snap = backend.observe()
            act = obstacle_agent.act(snap.observations["A"])
            steps += 1
            if act.channel is ActionChannel.REPORT:
                out = backend.handle_report(act)
            else:
                out = backend.execute(act)
                curr_n = act.payload["north_m"]
                curr_e = act.payload["east_m"]
                total_distance += math.hypot(curr_n - prev_pos[0], curr_e - prev_pos[1])
                prev_pos = (curr_n, curr_e)

            self.assertTrue(
                out.succeeded, f"ObstacleAware move step {step} failed unexpectedly: {out.error}"
            )
            obs_traj.append(
                {"actions": [act.to_dict()], "outcomes": [vars(out)], "before": snap.to_dict()}
            )

            if task.complete(backend.observe(), scen.truth):
                obs_success = True
                break

        self.assertTrue(obs_success, "ObstacleAware agent should successfully find the target")
        metrics = evaluator.evaluate(
            {
                "episode_id": "head-to-head-7",
                "status": "success",
                "truth": scen.truth,
                "trajectory": obs_traj,
                "final_snapshot": backend.observe().to_dict(),
            }
        )
        self.assertEqual(metrics["spatial/search_success_ratio"], 1.0)
        self.assertEqual(metrics["spatial/search_false_positive_count"], 0.0)
        self.assertLessEqual(steps, 6)
        self.assertGreater(total_distance, 0.0)

    def test_16_tiny_lane_spacing_estimated_scale_rejected_fast(self):
        """Scenario 16: Bounded scale: tiny lane_spacing_m is rejected in O(1) time without memory blowup."""
        obs = _make_obs(north=0.0, east=0.0)

        # 1. Extremely tiny spacing (e.g. 1e-9) estimated scale check
        agent_tiny = create_agent(config={"lane_spacing_m": 1e-9})
        with self.assertRaises(ContractValidationError) as ctx:
            agent_tiny.act(obs)
        self.assertIn("exceed", str(ctx.exception).lower())
        self.assertIn("limit", str(ctx.exception).lower())

        # 2. Moderately small spacing exceeding 500 lanes in 6m span (e.g. 0.005m -> 1200 lanes)
        agent_dense = create_agent(config={"lane_spacing_m": 0.005})
        with self.assertRaises(ContractValidationError) as ctx:
            agent_dense.act(obs)
        self.assertIn("exceed", str(ctx.exception).lower())

    def test_17_excessive_obstacle_count_rejected_at_construction(self):
        """Scenario 17: Bounded workload: obstacle count exceeding limit (50) rejected immediately."""
        too_many_obstacles = [
            {"center_north_m": float(i % 6), "center_east_m": float((i * 2) % 6), "radius_m": 0.2}
            for i in range(51)
        ]
        with self.assertRaises(ContractValidationError) as ctx:
            create_agent(config={"obstacles": too_many_obstacles})
        self.assertIn("obstacle count", str(ctx.exception).lower())
        self.assertIn("50", str(ctx.exception))

    def test_18_partially_blocked_waypoint_fails_explicitly_without_silent_coverage_drop(self):
        """Scenario 18: Obstacle directly blocking a lane waypoint raises explicit error rather than silently dropping it."""
        # Place obstacle directly covering waypoint (6.0, 3.0)
        lane_blocked_obstacle = [
            {"center_north_m": 6.0, "center_east_m": 3.0, "radius_m": 0.5}
        ]
        agent = create_agent(
            config={
                "lane_spacing_m": 3.0,
                "safety_margin_m": 0.2,
                "obstacles": lane_blocked_obstacle,
            }
        )
        obs = _make_obs(north=0.0, east=0.0)
        with self.assertRaises(ContractValidationError) as ctx:
            agent.act(obs)

        err_msg = str(ctx.exception).lower()
        self.assertIn("blocked", err_msg)
        self.assertIn("6.00", str(ctx.exception))
        self.assertIn("3.00", str(ctx.exception))

    def test_19_flight_down_m_out_of_search_area_bounds_rejected(self):
        """Scenario 19: Validates flight_down_m against search_area down bounds [-4.0, -1.0]."""
        obs = _make_obs(north=0.0, east=0.0)

        # 1. Above ceiling (-0.5m > -1.0m upper down bound)
        agent_high = create_agent(config={"flight_down_m": -0.5})
        with self.assertRaises(ContractValidationError) as ctx:
            agent_high.act(obs)
        self.assertIn("flight_down_m", str(ctx.exception).lower())
        self.assertIn("outside", str(ctx.exception).lower())

        # 2. Below floor (-5.0m < -4.0m lower down bound)
        agent_low = create_agent(config={"flight_down_m": -5.0})
        with self.assertRaises(ContractValidationError) as ctx:
            agent_low.act(obs)
        self.assertIn("flight_down_m", str(ctx.exception).lower())
        self.assertIn("outside", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
