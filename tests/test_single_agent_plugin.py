"""One external component composes with the installed-style builtin roles."""

from __future__ import annotations

import json
from pathlib import Path

from core import plugins
from core.conformance import validate_component_cases
from core.plugin_cli import run_multi
from core.plugins import PluginRegistry
from core.store import EpisodeStore


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "single_agent_plugin"


def test_single_agent_manifest_and_mixed_episode(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SAMPLE / "src"))
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    registry = PluginRegistry.discover(manifest_paths=(
        ROOT / "builtin_pack" / "drone_plugin.json",
        SAMPLE / "src" / "drone_single_agent" / "drone_plugin.json",
    ))
    assert not registry.issues
    descriptor = registry.resolve("sample.single/agent", "agent", {})
    assert descriptor.pack_id == "sample.single"
    cases = json.loads((SAMPLE / "component-cases.json").read_text(encoding="utf-8"))
    case = validate_component_cases(registry, cases)["sample.single/agent"]
    assert case["status"] == "passed", case

    config = json.loads((SAMPLE / "mixed-run.json").read_text(encoding="utf-8"))
    result = run_multi(config, tmp_path, registry=registry)
    assert result["success"] is True
    assert EpisodeStore.read_result(tmp_path, result["episode_id"]) == result
    run_record = json.loads((tmp_path / result["episode_id"] / "run.json").read_text(encoding="utf-8"))
    assert run_record["components"]["agent:central"]["id"] == "sample.single/agent"
