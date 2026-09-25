"""Inspect, preflight, and run installed v0.2 plugin combinations."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from typing import Any, Callable, Mapping
from uuid import uuid4

from contracts.data_v02 import ActionChannel, AgentBindingV02, CapabilitySetV02, ScenarioSpecV02
from contracts.model import ContractValidationError

from .assembly import discover_plugins
from .conformance import validate_plugin
from .episode_lifecycle import EpisodeLifecycle
from .multi_vehicle import EpisodeStatus, MultiVehicleEpisode
from .plugins import (PluginRegistry, PluginRegistryError, RegistryIssue,
                      _capability_issues, _validate_requirements)
from .session import SessionContext
from .store import EpisodeStore, StoreError


def _config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("configuration must be an object")
    return value


def _selection(value: object, kind: str) -> tuple[str, dict[str, Any]]:
    if not isinstance(value, dict) or set(value) - {"id", "config"} or "id" not in value:
        raise ValueError(f"{kind} must have id and optional config")
    config = value.get("config", {})
    if not isinstance(value["id"], str) or not isinstance(config, dict):
        raise ValueError(f"{kind} id/config has invalid type")
    return value["id"], config


def _multi_selections(data: Mapping[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    components = data.get("components")
    required = {"backend", "scenario", "task", "agents"}
    if (not isinstance(components, dict) or not required <= set(components)
            or set(components) - (required | {"runtime"})):
        raise ValueError("components must select backend, scenario, task, agents, and optional runtime")
    selected = {kind: _selection(components[kind], kind)
                for kind in ("backend", "scenario", "task")}
    if "runtime" in components:
        selected["runtime"] = _selection(components["runtime"], "runtime")
    agents = components["agents"]
    if not isinstance(agents, dict) or not agents:
        raise ValueError("components.agents must be a nonempty object")
    selected.update({f"agent:{name}": _selection(item, f"agents.{name}")
                     for name, item in agents.items()})
    return selected


def _combined_requirements(registry: PluginRegistry,
                           selected: Mapping[str, tuple[str, dict[str, Any]]],
                           data: Mapping[str, Any]) -> dict[str, Any]:
    actions = data.get("required_action_kinds", [])
    resources = data.get("required_sensor_resources", {})
    hard_cancel = data.get("require_hard_cancel", False)
    if not isinstance(actions, list) or not isinstance(resources, dict):
        raise ValueError("required_action_kinds must be an array and required_sensor_resources an object")
    explicit = _validate_requirements({
        "action_kinds": actions, "sensor_resources": resources, "hard_cancel": hard_cancel,
    }, "configuration")
    merged: dict[str, Any] = {"min_vehicles": len(data["vehicles"])}
    def add(requirements: Mapping[str, Any], source: str) -> None:
        for key, value in requirements.items():
            if key in ("action_kinds", "sensor_types", "time_bases", "scenario_operations"):
                merged[key] = list(dict.fromkeys((*merged.get(key, ()), *value)))
            elif key == "sensor_resources":
                current = merged.setdefault(key, {})
                for resource_id, kind in value.items():
                    if resource_id in current and current[resource_id] != kind:
                        raise PluginRegistryError((RegistryIssue(
                            "conflicting_capability_requirement", source,
                            f"sensor resource {resource_id} has incompatible required kinds",
                            {"field": key, "resource_id": resource_id}),))
                    current[resource_id] = kind
            elif key == "min_vehicles":
                merged[key] = max(merged[key], value)
            elif key in ("truth_access", "bounded_execution", "hard_cancel"):
                merged[key] = merged.get(key, False) or value
            elif key in merged and merged[key] != value:
                raise PluginRegistryError((RegistryIssue(
                    "conflicting_capability_requirement", source,
                    f"incompatible requirements for {key}", {"field": key}),))
            else:
                merged[key] = value
    for role, (identifier, _) in selected.items():
        if role == "task" or role == "scenario" or role.startswith("agent:"):
            descriptor = registry.resolve(identifier)
            add(descriptor.requires, descriptor.source)
    add(explicit, "configuration")
    return merged


def _preflight(registry: PluginRegistry, data: Mapping[str, Any], *,
               enable_backend: bool = False) -> dict[str, tuple[str, dict[str, Any]]]:
    selected = _multi_selections(data)
    report = registry.preflight({identifier: config for identifier, config in selected.values()})
    if not report.ok:
        raise PluginRegistryError(report.issues)
    for role, (identifier, config) in selected.items():
        expected_type = ("agent" if role.startswith("agent:") else
                         "runtime_provider" if role == "runtime" else role)
        registry.resolve(identifier, expected_type, config)
    descriptor = registry.resolve(selected["backend"][0], "backend")
    if descriptor.capabilities.get("requires_explicit_enable") and not enable_backend:
        raise PluginRegistryError((RegistryIssue(
            "explicit_enable_required", descriptor.source,
            f"{descriptor.id} requires --enable-backend"),))
    vehicles = data.get("vehicles")
    if not isinstance(vehicles, list) or not vehicles:
        raise ValueError("vehicles must be a nonempty array")
    requirements = _combined_requirements(registry, selected, data)
    required = requirements.get("action_kinds", [])
    declared = descriptor.capabilities
    if "max_vehicles" in declared and len(vehicles) > declared["max_vehicles"]:
        raise PluginRegistryError((RegistryIssue(
            "insufficient_capacity", descriptor.source,
            f"{descriptor.id} declares capacity {declared['max_vehicles']}"),))
    if ("action_kinds" in declared and all(isinstance(kind, str) for kind in required)
            and not set(required).issubset(declared["action_kinds"])):
        raise PluginRegistryError((RegistryIssue(
            "unsupported_action", descriptor.source,
            f"{descriptor.id} does not declare all required actions"),))
    registry.resolve(selected["backend"][0], "backend", selected["backend"][1],
                     requirements=requirements)
    return selected


def run_multi(data: Mapping[str, Any], output: Path,
              *, registry: PluginRegistry | None = None,
              enable_backend: bool = False,
              on_event: Callable[[dict[str, object]], None] | None = None,
              cancel_event: Event | None = None) -> dict[str, object]:
    registry = registry or discover_plugins()
    selected = _preflight(registry, data, enable_backend=enable_backend)
    requirements = _combined_requirements(registry, selected, data)
    episode_id = data.get("episode_id") or uuid4().hex
    if not isinstance(episode_id, str):
        raise ValueError("episode_id must be a string")
    vehicles = data.get("vehicles")
    bindings_data = data.get("bindings")
    max_steps = data.get("max_steps", 1)
    if not isinstance(vehicles, list) or not isinstance(bindings_data, list):
        raise ValueError("vehicles and bindings must be arrays")
    bindings = tuple(AgentBindingV02.from_dict(row) for row in bindings_data)
    store = EpisodeStore(output)
    store.start(episode_id, metadata={
        "plugin_api": "drone.plugin.api/v0.2",
        "components": {role: registry.resolve(identifier).to_dict()
                       for role, (identifier, _) in selected.items()},
        "config": {
            role: registry.recordable_config(identifier, config)
            for role, (identifier, config) in selected.items()
        },
        "capability_requirements": requirements,
    })
    active = [True]
    lifecycle = EpisodeLifecycle(lambda event: store.append("events.jsonl", event),
                                 on_event=on_event, cancel_event=cancel_event)
    context = SessionContext(
        episode_id, store,
        lambda kind, fields: lifecycle.emit("plugin", "plugin_event",
                                            name=kind, payload=dict(fields)),
        lambda: active[0],
    )
    def create(role: str):
        identifier, config = selected[role]
        return registry.instantiate(identifier, config,
                                    expected_type=("agent" if role.startswith("agent:") else
                                                   "runtime_provider" if role == "runtime" else role),
                                    context=context)
    scenario_item = None
    runtime_item = None
    backend = None
    task = None
    agents: dict[str, object] = {}
    run_started = False
    try:
        scenario_item = create("scenario")
        scenario = getattr(scenario_item, "spec", scenario_item)
        truth = getattr(scenario_item, "truth", {})
        if not isinstance(scenario, ScenarioSpecV02):
            raise ValueError("scenario plugin must provide ScenarioSpecV02 or .spec")
        for role in selected:
            if role.startswith("agent:"):
                agents[role.removeprefix("agent:")] = create(role)
        task = create("task")
        if "runtime" in selected:
            runtime_item = create("runtime")
        backend = create("backend")
        capability = backend.capabilities()
        if not isinstance(capability, CapabilitySetV02):
            raise ValueError("backend.capabilities must return CapabilitySetV02")
        backend_descriptor = registry.resolve(selected["backend"][0], "backend")
        actual = capability.to_dict()
        if "scenario_id" in backend_descriptor.capabilities:
            actual["scenario_id"] = backend_descriptor.capabilities["scenario_id"]
        runtime_issues = _capability_issues(
            replace(backend_descriptor, capabilities=actual), requirements)
        if runtime_issues:
            raise PluginRegistryError(runtime_issues)
        action_handlers: dict[object, object] = {}
        for owner, component in (("backend", backend), ("task", task)):
            provided = getattr(component, "action_handlers", {})
            if callable(provided):
                provided = provided()
            if not isinstance(provided, Mapping):
                raise ValueError(f"{owner}.action_handlers must be a mapping")
            for channel, handler in provided.items():
                if not isinstance(channel, ActionChannel) or channel is ActionChannel.CONTROL or not callable(handler):
                    raise ValueError(f"{owner}.action_handlers has invalid channel or handler")
                if channel in action_handlers:
                    raise ValueError(f"duplicate action handler for {channel}")
                action_handlers[channel] = handler
        store.write_artifact("metadata/scenario.json",
                             (json.dumps(scenario.to_dict(), ensure_ascii=False) + "\n").encode("utf-8"))
        store.write_artifact("metadata/capabilities.json",
                             (json.dumps(capability.to_dict(), ensure_ascii=False) + "\n").encode("utf-8"))
        engine = MultiVehicleEpisode(
            backend, capability, scenario, agents, bindings, task,
            vehicle_ids=vehicles,
            required_sensor_resources=requirements.get("sensor_resources", {}),
            required_action_kinds=requirements.get("action_kinds", []),
            action_schemas=data.get("action_schemas", {}),
            payload_schemas=data.get("payload_schemas", {}),
            action_handlers=action_handlers,
            truth=truth,
            lifecycle=lifecycle,
            runtime=runtime_item,
            episode_id=episode_id,
            cleanup_components=(("scenario.close", scenario_item),),
        )
        run_started = True
        outcome = engine.run(max_steps=max_steps, time_budget_s=data.get("time_budget_s"))
        for index, step in enumerate(outcome.steps):
            store.append("trajectory.jsonl", {
                "sequence": index, "before": step.before.to_dict(),
                "actions": [action.to_dict() for action in step.actions],
                "outcomes": [vars(item) for item in step.outcomes],
                "after": step.after.to_dict(),
            })
        result: dict[str, object] = {
            "schema": "drone.platform.contract/v0.2",
            "episode_id": episode_id,
            "status": outcome.status.value,
            "success": outcome.status is EpisodeStatus.SUCCESS,
            "step_count": len(outcome.steps),
            "final_snapshot": outcome.final_snapshot.to_dict(),
            "cleanup_errors": list(outcome.cleanup_errors),
            "record_path": "trajectory.jsonl",
        }
        store.finish(result)
        active[0] = False
        return result
    except BaseException as exc:
        active[0] = False
        if not run_started:
            lifecycle.close(((name, component) for name, component in
                             (("backend.close", backend), ("task.close", task),
                              *((f"agent:{name}.close", agent) for name, agent in agents.items()),
                              ("scenario.close", scenario_item))
                             if component is not None and getattr(component, "close", None)),
                            runtime=runtime_item)
            lifecycle.end()
        try:
            lifecycle.emit("runner", "component_error", stage="run_multi",
                           error=f"{type(exc).__name__}: {exc}")
        except (OSError, StoreError):
            pass
        raise


def _runtime_negative_checks(data: Mapping[str, Any], registry: PluginRegistry) -> tuple[dict[str, bool], list[str]]:
    """Exercise rejection paths against the sample without retaining probe records."""
    checks: dict[str, bool] = {}
    skipped: list[str] = []
    with TemporaryDirectory(prefix="drone-plugin-conformance-") as temporary:
        root = Path(temporary)
        store = EpisodeStore(root / "artifact-probe")
        store.start("path-rejection")
        try:
            store.write_artifact("../outside", b"should not be written")
        except ValueError:
            pass
        else:
            raise ValueError("artifact path traversal was accepted")
        if (root / "artifact-probe" / "outside").exists():
            raise ValueError("artifact escaped its episode directory")
        checks["artifact_path_rejection"] = True

        selected = _multi_selections(data)
        backend = registry.resolve(selected["backend"][0], "backend")
        if backend.capabilities.get("requires_explicit_enable"):
            skipped.append("action_schema_rejection")
            return checks, skipped
        required = data.get("required_action_kinds")
        schemas = data.get("action_schemas")
        if (not isinstance(required, list) or not required or
                not isinstance(schemas, dict) or any(kind not in schemas for kind in required)):
            raise ValueError("conformance sample must declare required actions and their schema IDs")
        invalid = deepcopy(dict(data))
        invalid["episode_id"] = "invalid-action-schema"
        for kind in required:
            invalid["action_schemas"][kind] = f"{schemas[kind]}-conformance-invalid"
        probe_root = root / "action-probe"
        try:
            run_multi(invalid, probe_root, registry=registry)
        except ContractValidationError as exc:
            if "unregistered action payload schema" not in str(exc):
                raise ValueError(f"action schema probe failed for a different reason: {exc}") from exc
        else:
            raise ValueError("mismatched action schema was accepted")
        episode = probe_root / invalid["episode_id"]
        if not (episode / "INCOMPLETE").is_file() or (episode / "result.json").exists():
            raise ValueError("rejected action did not leave an incomplete episode record")
        checks["action_schema_rejection"] = True
        checks["failure_incomplete_record"] = True
    return checks, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="list discovered plugin components")
    listing.add_argument("--manifest", action="append", type=Path, default=[])
    preflight = sub.add_parser("preflight", help="validate a v0.2 combination before starting a backend")
    preflight.add_argument("config", type=Path)
    preflight.add_argument("--manifest", action="append", type=Path, default=[])
    preflight.add_argument("--enable-backend", action="store_true")
    validate = sub.add_parser("validate-plugin", help="validate a plugin manifest and optional sample")
    validate.add_argument("--manifest", action="append", type=Path, default=[])
    validate.add_argument("--sample", type=Path)
    validate.add_argument("--component-cases", type=Path,
                          help="JSON map of component IDs to config/probe cases for executable hook checks")
    validate.add_argument("--output", type=Path, default=Path("runs"))
    validate.add_argument("--enable-backend", action="store_true")
    running = sub.add_parser("run-multi", help="run a v0.2 multi-vehicle episode")
    running.add_argument("config", type=Path)
    running.add_argument("--output", type=Path, default=Path("runs"))
    running.add_argument("--manifest", action="append", type=Path, default=[])
    running.add_argument("--enable-backend", action="store_true")
    show = sub.add_parser("show-result", help="read one completed v0.2 episode result")
    show.add_argument("episode_id")
    show.add_argument("--output", type=Path, default=Path("runs"))
    args = parser.parse_args(argv)
    try:
        if args.command == "show-result":
            print(json.dumps(EpisodeStore.read_result(args.output, args.episode_id),
                             ensure_ascii=False))
            return 0
        manifests = getattr(args, "manifest", [])
        registry = (PluginRegistry.discover(tuple(manifests))
                    if manifests else discover_plugins())
        if args.command == "list":
            print(json.dumps({"components": [item.to_dict() for item in registry.list()],
                              "issues": [issue.to_dict() for issue in registry.issues]},
                             ensure_ascii=False))
            return int(bool(registry.issues))
        if args.command == "validate-plugin":
            report = validate_plugin(
                registry,
                sample=_config(args.sample) if args.sample else None,
                output=args.output,
                component_cases=_config(args.component_cases) if args.component_cases else None,
                enable_backend=args.enable_backend,
            )
            print(json.dumps(report, ensure_ascii=False))
            return int(report["status"] == "failed" or
                       ((args.sample is not None or args.component_cases is not None)
                        and not report["valid"]))
        data = _config(args.config)
        if args.command == "preflight":
            selected = _preflight(registry, data, enable_backend=args.enable_backend)
            print(json.dumps({"ok": True, "components": list(selected)}, ensure_ascii=False))
            return 0
        result = run_multi(data, args.output, registry=registry,
                           enable_backend=args.enable_backend)
        print(json.dumps(result, ensure_ascii=False))
        return int(not result["success"])
    except (OSError, ValueError, PluginRegistryError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
