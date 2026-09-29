"""No-simulator contract and integration checks for the design Pack."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import pytest

from contracts.data_v02 import ActionChannel, ActionV02
from core.plugin_cli import _preflight, run_multi
from core.plugins import PluginRegistry, PluginRegistryError
from core.store import EpisodeStore
from builtin_pack.v02 import scenario_spec


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "examples" / "drone_design_pack"
SOURCE = PACK / "src"


@pytest.fixture(autouse=True)
def _source_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(SOURCE))


sys.path.insert(0, str(SOURCE))
try:
    from drone_design import DesignedMockBackend
finally:
    sys.path.remove(str(SOURCE))


def _registry():
    return PluginRegistry.discover(manifest_paths=(
        SOURCE / "drone_design" / "drone_plugin.json",
        ROOT / "builtin_pack" / "drone_plugin.json",
    ))


def _action(vehicle="A", target=5, action_id="move"):
    return ActionV02(action_id, vehicle, "drone/move", ActionChannel.CONTROL,
                     "drone.move/v1", {"north_m": target}, 1.0)


def _state(backend, vehicle="A"):
    return backend.observe().observations[vehicle].state


def test_design_parameters_change_movement_and_battery_and_reset():
    fast = DesignedMockBackend({"max_speed_mps": 2, "step_duration_s": 1,
                                "battery_capacity_wh": 6})
    slow = DesignedMockBackend({"max_speed_mps": 1, "step_duration_s": 1,
                                "battery_capacity_wh": 6})
    for backend in (fast, slow):
        initial = backend.reset(scenario_spec(7), ("A", "B"))
        assert initial.sequence == 0
        assert _state(backend, "B")["battery_remaining_wh"] == 6
        assert backend.execute(_action()).succeeded
    assert _state(fast)["north_m"] == 2
    assert _state(fast)["battery_remaining_wh"] == 4
    assert _state(slow)["north_m"] == 1
    assert _state(slow)["battery_remaining_wh"] == 5
    assert _state(fast, "B")["north_m"] == 0
    fast.reset(scenario_spec(7), ("A", "B"))
    assert _state(fast)["north_m"] == 0
    assert _state(fast)["battery_remaining_wh"] == 6
    fast.close()
    slow.close()


def test_battery_capacity_limits_range_and_rejects_depleted_move():
    backend = DesignedMockBackend({"max_speed_mps": 3, "step_duration_s": 1,
                                   "battery_capacity_wh": 2})
    backend.reset(scenario_spec(7), ("A",))
    assert backend.execute(_action()).succeeded
    assert _state(backend)["north_m"] == 2
    assert _state(backend)["battery_remaining_wh"] == 0
    assert not backend.execute(_action(action_id="depleted")).succeeded
    assert backend.observe().sequence == 1
    backend.close()


def test_manifest_rejects_invalid_design_before_factory_import():
    registry = _registry()
    assert registry.issues == ()
    sample = json.loads((PACK / "sample.json").read_text(encoding="utf-8"))
    assert _preflight(registry, sample)
    assert registry.effective_config("sample.drone-design/backend", None) == {
        "max_speed_mps": 2.0, "step_duration_s": 1.0,
        "battery_capacity_wh": 6.0,
    }
    sample["components"]["backend"].pop("config")
    assert _preflight(registry, sample)
    sample["components"]["backend"]["config"] = {
        "max_speed_mps": 2, "step_duration_s": 1, "battery_capacity_wh": 6}
    sample["components"]["backend"]["config"]["max_speed_mps"] = 0
    sys.modules.pop("drone_design", None)
    with pytest.raises(PluginRegistryError) as error:
        _preflight(registry, sample)
    assert error.value.issues[0].code == "invalid_config"
    assert "drone_design" not in sys.modules


def test_builtin_components_run_with_design_backend_and_result_rereads():
    sample = json.loads((PACK / "sample.json").read_text(encoding="utf-8"))
    with TemporaryDirectory() as directory:
        result = run_multi(sample, Path(directory), registry=_registry())
        assert result["success"] is True
        assert result["step_count"] == 3
        assert EpisodeStore.read_result(directory, result["episode_id"]) == result
        observations = result["final_snapshot"]["observations"]
        for vehicle in ("A", "B"):
            state = observations[vehicle]["state"]
            assert state["north_m"] == 5
            assert state["battery_remaining_wh"] == 1
