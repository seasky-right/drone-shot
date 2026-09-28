"""Feasible generation and reset behavior of the independent spatial Pack."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from contracts.data_v02 import ActionChannel, ActionV02, ScenarioSpecV02
from core.plugins import PluginRegistry, PluginRegistryError


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "spatial_world_pack" / "src"
sys.path.insert(0, str(SOURCE))
try:
    from drone_spatial_world import (  # noqa: E402
        ACTION_KIND, ACTION_SCHEMA, REPORT_KIND, REPORT_SCHEMA,
        SearchGenerator, SearchMockBackend, SpatialGenerator, SpatialMockBackend,
        _segment_clear, _world, feasible_pairs,
    )
finally:
    sys.path.remove(str(SOURCE))


def _action(target, *, action_id="move", kind=ACTION_KIND, channel=ActionChannel.CONTROL,
            schema=ACTION_SCHEMA, vehicle="A"):
    return ActionV02(action_id, vehicle, kind, channel, schema, target, 1.0)


def test_seed_generates_distinct_reproducible_feasible_instances():
    generator = SpatialGenerator()
    world, world_checksum = _world()
    first, repeat, second = (generator.generate(seed) for seed in (7, 7, 11))
    assert first.spec.to_dict() == repeat.spec.to_dict()
    assert first.spec.checksum != second.spec.checksum
    assert first.spec.initial_states != second.spec.initial_states
    assert first.spec.seed == 7
    assert first.spec.initial_states["A"]["instance_id"] == first.spec.checksum[:16]
    assert first.spec.initial_states["A"]["world_checksum"] == world_checksum
    assert first.spec.initial_states["A"]["goal"] == first.truth["goal"]
    assert first.spec.resources == {}
    assert len(feasible_pairs(world)) > 1
    for scenario in (first, second):
        initial = scenario.spec.initial_states["A"]
        start = {key: initial[key] for key in ("north_m", "east_m", "down_m")}
        assert _segment_clear(world, start, scenario.truth["goal"])
    generator.close()


def test_generator_rejects_invalid_seed_and_impossible_world(monkeypatch):
    generator = SpatialGenerator()
    for seed in (-1, True, 1.0):
        with pytest.raises(ValueError, match="seed"):
            generator.generate(seed)
    import drone_spatial_world
    world, checksum = _world()
    world["generation"]["goal_sites"] = []
    monkeypatch.setattr(drone_spatial_world, "_world", lambda: (world, checksum))
    with pytest.raises(ValueError, match="no feasible"):
        generator.generate(1)


def test_backend_reset_observation_actions_and_lifecycle():
    backend = SpatialMockBackend()
    scenario = SpatialGenerator().generate(7)
    capabilities = backend.capabilities()
    assert capabilities.action_kinds == (ACTION_KIND,)
    assert capabilities.coordinate_frame == "local_ned"
    assert capabilities.scenario_operations == ("load", "reset")
    assert capabilities.truth_access is True
    initial = backend.reset(scenario.spec, ("A",))
    state = initial.observations["A"].state
    assert state["goal"] == scenario.truth["goal"]
    assert "truth" not in initial.to_dict()
    assert "world_checksum" not in state
    assert not initial.observations["A"].sensors
    goal = state["goal"]
    outcome = backend.execute(_action(goal))
    assert outcome.succeeded
    assert backend.observe().observations["A"].state["north_m"] == goal["north_m"]
    assert backend.observe().sequence == 1
    reset = backend.reset(scenario.spec, ("A",))
    assert reset.sequence == 0
    assert reset.observations["A"].state == state
    backend.close()
    assert backend.closed
    with pytest.raises(RuntimeError, match="closed"):
        backend.reset(scenario.spec, ("A",))


def test_backend_rejects_illegal_move_without_mutating_position():
    backend = SpatialMockBackend()
    scenario = SpatialGenerator().generate(7)
    before = backend.reset(scenario.spec, ("A",)).observations["A"].state
    targets = [
        _action({"north_m": 99, "east_m": 0, "down_m": -2}, action_id="bounds"),
        _action({"north_m": 3, "east_m": 3, "down_m": -2}, action_id="obstacle"),
        _action({"north_m": True, "east_m": 0, "down_m": -2}, action_id="type"),
        _action({"north_m": 0}, action_id="missing"),
        _action(before["goal"], action_id="channel", channel=ActionChannel.REPORT),
        _action(before["goal"], action_id="schema", schema="wrong/v1"),
        _action(before["goal"], action_id="vehicle", vehicle="B"),
    ]
    for action in targets:
        result = backend.execute(action)
        assert not result.succeeded and result.error
        assert backend.observe().observations["A"].state == before
        assert backend.observe().sequence == 0
    backend.close()


def test_reset_rejects_tampered_or_mismatched_instance():
    scenario = SpatialGenerator().generate(7).spec
    backend = SpatialMockBackend()
    fields = scenario.to_dict()
    fields["initial_states"]["A"]["goal"]["north_m"] = 3.5
    with pytest.raises(ValueError, match="does not match"):
        backend.reset(ScenarioSpecV02.from_dict(fields), ("A",))
    with pytest.raises(ValueError, match="vehicle A"):
        backend.reset(scenario, ("B",))
    backend.close()


def test_manifest_capabilities_and_requirement_failure():
    manifest = SOURCE / "drone_spatial_world" / "drone_plugin.json"
    registry = PluginRegistry.discover(manifest_paths=(manifest,))
    assert registry.issues == ()
    backend = registry.resolve("sample.spatial-grid/backend", "backend")
    assert backend.capabilities["scenario_id"] == "sample.spatial-grid"
    registry.resolve(backend.id, requirements={"action_kinds": [ACTION_KIND],
                                               "coordinate_frame": "local_ned"})
    with pytest.raises(PluginRegistryError):
        registry.resolve(backend.id, requirements={"action_kinds": ["other/action"]})
    world = json.loads((SOURCE / "drone_spatial_world" / "world.json").read_text())
    assert world["public"]["bounds_m"] and world["generation"]["spawn_sites"]


def test_search_seed_modes_hide_truth_and_distinguish_detection_states():
    generator = SearchGenerator()
    world, _ = _world()
    checksums = set()
    for seed, expected in ((7, "empty"), (11, "missing"), (19, "detected")):
        scenario = generator.generate(seed)
        assert scenario.spec == generator.generate(seed).spec
        assert scenario.spec.scenario_id == "sample.spatial-search"
        assert "target" not in scenario.spec.to_dict()
        assert "goal" not in scenario.spec.to_dict()
        assert scenario.truth["target_id"] == "T1"
        checksums.add(scenario.spec.checksum)
        start = {key: scenario.spec.initial_states["A"][key]
                 for key in ("north_m", "east_m", "down_m")}
        assert _segment_clear(world, start, scenario.truth["target"])
        backend = SearchMockBackend()
        snapshot = backend.reset(scenario.spec, ("A",))
        observation = snapshot.observations["A"]
        assert "target" not in observation.to_dict()
        assert observation.state["search_area"] == world["public"]["bounds_m"]
        if expected == "empty":
            assert observation.state["detections"] == []
            assert observation.missing_sensors == {}
        elif expected == "missing":
            assert observation.state["detections"] == []
            assert observation.missing_sensors == {"search-detector": "unavailable"}
        else:
            clue = observation.state["detections"][0]
            assert clue["north_m"] != scenario.truth["target"]["north_m"]
            assert clue["east_m"] != scenario.truth["target"]["east_m"]
            assert observation.missing_sensors == {}
        backend.close()
    assert len(checksums) == 3


def test_search_report_is_echoed_without_correctness_leak_and_reset_clears_it():
    scenario = SearchGenerator().generate(19)
    backend = SearchMockBackend()
    first = backend.reset(scenario.spec, ("A",))
    assert set(backend.capabilities().action_kinds) == {ACTION_KIND, REPORT_KIND}
    assert ActionChannel.REPORT in backend.action_handlers
    clue = first.observations["A"].state["detections"][0]
    report = _action({key: clue[key] for key in ("north_m", "east_m", "down_m")},
                     kind=REPORT_KIND, channel=ActionChannel.REPORT, schema=REPORT_SCHEMA)
    assert backend.handle_report(report).succeeded
    observed = backend.observe()
    assert observed.sequence == 1
    assert observed.observations["A"].state["reports"] == [
        {"target_id": "T1", **report.payload}]
    assert "matched" not in observed.to_dict()
    assert backend.reset(scenario.spec, ("A",)).observations["A"].state["reports"] == []
    bad = _action({"north_m": 0}, kind=REPORT_KIND,
                  channel=ActionChannel.REPORT, schema=REPORT_SCHEMA)
    assert not backend.handle_report(bad).succeeded
    assert backend.observe().sequence == 1
    assert backend.observe().observations["A"].state["reports"] == []
    backend.close()


def test_search_reset_rejects_tampered_public_initial_state():
    scenario = SearchGenerator().generate(7).spec
    fields = scenario.to_dict()
    fields["initial_states"]["A"]["north_m"] = 3.5
    with pytest.raises(ValueError, match="does not match"):
        SearchMockBackend().reset(ScenarioSpecV02.from_dict(fields), ("A",))
