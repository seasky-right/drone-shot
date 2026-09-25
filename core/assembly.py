"""Resolve episode components from installed plugin metadata."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path
from typing import Any, Mapping

from contracts import BackendConfig, TaskSpec

from .plugins import PluginRegistry, PluginRegistryError
from .recorder import Recorder
from .runner import EpisodeRunner


def discover_plugins() -> PluginRegistry:
    # A source checkout has no installed pack entry point until the pack is built.
    # Installed Core wheels have no sibling builtin_pack directory.
    root = Path(__file__).resolve().parents[1]
    source_manifest = root / "builtin_pack" / "drone_plugin.json"
    try:
        metadata.distribution("drone-builtin-pack")
    except metadata.PackageNotFoundError:
        paths = (source_manifest,) if source_manifest.is_file() else ()
    else:
        paths = ()
    return PluginRegistry.discover(manifest_paths=paths)


def _legacy_component(registry: PluginRegistry, kind: str, key: str, value: str,
                      *, task_type: str | None = None) -> str:
    matches = [
        item.id for item in registry.list()
        if item.type == kind and item.capabilities.get(key) == value
        and (task_type is None or item.capabilities.get("legacy_task_type") == task_type)
    ]
    if len(matches) != 1:
        if kind == "backend" and key == "legacy_backend_type":
            raise ValueError(f"unsupported backend_type={value!r}; no installed backend plugin matches")
        raise ValueError(f"unsupported {kind} {value!r}: expected exactly one installed plugin match")
    return matches[0]


def _selection(value: object, kind: str) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping) or set(value) - {"id", "config"} or "id" not in value:
        raise ValueError(f"components.{kind} must contain id and optional config")
    identifier, config = value["id"], value.get("config", {})
    if not isinstance(identifier, str) or not isinstance(config, Mapping):
        raise ValueError(f"components.{kind} has invalid id or config")
    return identifier, config


def resolve_episode_components(registry: PluginRegistry, spec: TaskSpec,
                               backend_config: BackendConfig, agent_name: str,
                               components: Mapping[str, object] | None = None,
                               *, enable_airsim: bool = False
                               ) -> tuple[dict[str, object], dict[str, object]]:
    if components is not None and (not isinstance(components, Mapping)
                                   or set(components) != {"backend", "task", "agent", "evaluator"}):
        raise ValueError("components must select backend, task, agent, and evaluator")
    if components is None:
        ids = {
            "backend": _legacy_component(registry, "backend", "legacy_backend_type",
                                         backend_config.backend_type),
            "task": _legacy_component(registry, "task", "legacy_task_type", spec.task_type),
            "agent": _legacy_component(registry, "agent", "legacy_alias", agent_name,
                                       task_type=spec.task_type),
            "evaluator": _legacy_component(registry, "evaluator", "legacy_task_type",
                                           spec.task_type),
        }
        selected = {kind: (identifier, {}) for kind, identifier in ids.items()}
    else:
        selected = {kind: _selection(value, kind) for kind, value in components.items()}
    preflight = registry.preflight({identifier: config for identifier, config in selected.values()})
    if not preflight.ok:
        raise PluginRegistryError(preflight.issues)
    for kind, (identifier, config) in selected.items():
        descriptor = registry.resolve(identifier, expected_type=kind, config=config)
        if descriptor.capabilities.get("requires_explicit_enable") and not enable_airsim:
            raise ValueError("backend_type=airsim requires --enable-airsim (connects and commands a simulated vehicle)")
    result: dict[str, object] = {}
    provenance: dict[str, object] = {}
    try:
        for kind in ("task", "agent", "evaluator", "backend"):
            identifier, config = selected[kind]
            kwargs = {"spec": spec} if kind in ("agent", "evaluator") else {}
            result[kind] = registry.instantiate(identifier, config, expected_type=kind, **kwargs)
            provenance[kind] = registry.resolve(identifier).to_dict()
    except BaseException:
        for component in reversed(tuple(result.values())):
            close = getattr(component, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    pass
        raise
    return result, provenance


def build_plugin_runner(spec: TaskSpec, backend_config: BackendConfig,
                        agent_name: str, recorder: Recorder, *,
                        components: Mapping[str, object] | None = None,
                        enable_airsim: bool = False,
                        registry: PluginRegistry | None = None) -> EpisodeRunner:
    registry = registry or discover_plugins()
    resolved, provenance = resolve_episode_components(
        registry, spec, backend_config, agent_name,
        components, enable_airsim=enable_airsim)
    return EpisodeRunner(resolved["backend"], resolved["agent"], resolved["task"],
                         resolved["evaluator"], recorder, provenance=provenance)
