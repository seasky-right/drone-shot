"""Inspect, preflight, and run installed v0.2 plugin combinations."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import replace
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from contracts.data_v02 import ActionChannel, AgentBindingV02, CapabilitySetV02, EpisodeSnapshotV02, ScenarioSpecV02
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
    required = {"backend", "task", "agents"}
    optional = {"scenario", "generator", "runtime", "evaluator"}
    if (not isinstance(components, dict) or not required <= set(components)
            or set(components) - (required | optional)
            or ("scenario" in components) == ("generator" in components)):
        raise ValueError("components must select backend, task, agents, and exactly one scenario or generator")
    selected = {kind: _selection(components[kind], kind)
                for kind in ("backend", "task")}
    scene_role = "scenario" if "scenario" in components else "generator"
    selected[scene_role] = _selection(components[scene_role], scene_role)
    if "runtime" in components:
        selected["runtime"] = _selection(components["runtime"], "runtime")
    if "evaluator" in components:
        selected["evaluator"] = _selection(components["evaluator"], "evaluator")
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
    for role, (identifier, config) in selected.items():
        if role in ("task", "scenario", "generator") or role.startswith("agent:"):
            descriptor = registry.resolve(identifier, config=config)
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
                         "runtime_provider" if role == "runtime" else
                         "scenario_generator" if role == "generator" else role)
        registry.resolve(identifier, expected_type, config)
    descriptor = registry.resolve(selected["backend"][0], "backend")
    vehicles = data.get("vehicles")
    if not isinstance(vehicles, list) or not vehicles:
        raise ValueError("vehicles must be a nonempty array")
    requirements = _combined_requirements(registry, selected, data)
    required = requirements.get("action_kinds", [])
    declared = descriptor.capabilities
    scene_role = "scenario" if "scenario" in selected else "generator"
    scene_id = selected[scene_role][0]
    environment = registry.resolve(scene_id).requires.get("environment")
    if environment is not None and declared.get("environment") != environment:
        raise PluginRegistryError((RegistryIssue(
            "incompatible_environment", descriptor.source,
            f"{descriptor.id} cannot run {scene_id}: environment {environment} required",
            {"field": "environment", "backend": descriptor.id,
             "scenario_source": scene_id, "required": environment,
             "declared": declared.get("environment")}),))
    if "max_vehicles" in declared and len(vehicles) > declared["max_vehicles"]:
        raise PluginRegistryError((RegistryIssue(
            "insufficient_capacity", descriptor.source,
            f"{descriptor.id} declares capacity {declared['max_vehicles']}"),))
    if ("action_kinds" in declared and all(isinstance(kind, str) for kind in required)
            and not set(required).issubset(declared["action_kinds"])):
        raise PluginRegistryError((RegistryIssue(
            "unsupported_action", descriptor.source,
            f"{descriptor.id} does not declare all required actions"),))
    checked = _resource_requirements(descriptor, requirements)
    registry.resolve(selected["backend"][0], "backend", selected["backend"][1],
                     requirements=checked)
    if descriptor.capabilities.get("requires_explicit_enable") and not enable_backend:
        raise PluginRegistryError((RegistryIssue(
            "explicit_enable_required", descriptor.source,
            f"{descriptor.id} requires --enable-backend"),))
    return selected


def _resource_requirements(descriptor, requirements: Mapping[str, Any]) -> dict[str, Any]:
    checked = dict(requirements)
    if descriptor.capabilities.get("sensor_resource_confirmation") != "post_reset":
        return checked
    checked["sensor_types"] = list(dict.fromkeys((
        *requirements.get("sensor_types", ()),
        *requirements.get("sensor_resources", {}).values())))
    checked.pop("sensor_resources", None)
    return checked


def run_multi(data: Mapping[str, Any], output: Path,
              *, registry: PluginRegistry | None = None,
              enable_backend: bool = False,
              on_event: Callable[[dict[str, object]], None] | None = None,
              on_snapshot: Callable[[EpisodeSnapshotV02, Path], None] | None = None,
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
                                                   "runtime_provider" if role == "runtime" else
                                                   "scenario_generator" if role == "generator" else role),
                                    context=context)
    scenario_item = None
    generator = None
    runtime_item = None
    backend = None
    task = None
    agents: dict[str, object] = {}
    run_started = False
    try:
        if "generator" in selected:
            seed = data.get("seed")
            if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
                raise ValueError("generator runs require a nonnegative integer seed")
            generator = create("generator")
            scenario_item = generator.generate(seed)
        else:
            scenario_item = create("scenario")
        scenario = getattr(scenario_item, "spec", scenario_item)
        truth = getattr(scenario_item, "truth", {})
        if not isinstance(scenario, ScenarioSpecV02):
            raise ValueError("scenario plugin must provide ScenarioSpecV02 or .spec")
        if "generator" in selected and scenario.seed != seed:
            raise ValueError("generated scenario did not record the requested seed")
        scene_role = "generator" if generator is not None else "scenario"
        scene_descriptor = registry.resolve(selected[scene_role][0])
        declared_scenario = scene_descriptor.capabilities.get("scenario_id")
        if declared_scenario is not None and declared_scenario != scenario.scenario_id:
            raise ValueError("scenario ID differs from the selected component declaration")
        if not isinstance(truth, Mapping) or (scenario.truth_access == "none" and truth):
            raise ValueError("scenario truth is invalid for its access level")
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
        supported_scenario = backend_descriptor.capabilities.get("scenario_id")
        if supported_scenario is not None and supported_scenario != scenario.scenario_id:
            raise PluginRegistryError((RegistryIssue(
                "insufficient_capability", backend_descriptor.source,
                f"{backend_descriptor.id} cannot load scenario {scenario.scenario_id}",
                {"field": "scenario_id"}),))
        actual = capability.to_dict()
        if "scenario_id" in backend_descriptor.capabilities:
            actual["scenario_id"] = backend_descriptor.capabilities["scenario_id"]
        if "environment" in backend_descriptor.capabilities:
            actual["environment"] = backend_descriptor.capabilities["environment"]
        runtime_issues = _capability_issues(
            replace(backend_descriptor, capabilities=actual),
            _resource_requirements(backend_descriptor, requirements))
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
        cleanup_components = [("scenario.close", scenario_item)]
        if generator is not None and generator is not scenario_item:
            cleanup_components.append(("generator.close", generator))
        engine = MultiVehicleEpisode(
            backend, capability, scenario, agents, bindings, task,
            vehicle_ids=vehicles,
            required_sensor_resources=requirements.get("sensor_resources", {}),
            defer_sensor_resource_check=(backend_descriptor.capabilities.get(
                "sensor_resource_confirmation") == "post_reset"),
            on_confirmed_capabilities=lambda confirmed: store.write_artifact(
                "metadata/capabilities-confirmed.json",
                (json.dumps(confirmed.to_dict(), ensure_ascii=False) + "\n").encode("utf-8")),
            required_action_kinds=requirements.get("action_kinds", []),
            action_schemas=data.get("action_schemas", {}),
            payload_schemas=data.get("payload_schemas", {}),
            action_handlers=action_handlers,
            truth=deepcopy(dict(truth)),
            lifecycle=lifecycle,
            runtime=runtime_item,
            episode_id=episode_id,
            cleanup_components=cleanup_components,
            on_snapshot=(lambda snapshot: on_snapshot(snapshot, store.directory)) if on_snapshot else None,
        )
        run_started = True
        outcome = engine.run(max_steps=max_steps, time_budget_s=data.get("time_budget_s"))
        trajectory = []
        for index, step in enumerate(outcome.steps):
            record = {
                "sequence": index, "before": step.before.to_dict(),
                "actions": [action.to_dict() for action in step.actions],
                "outcomes": [vars(item) for item in step.outcomes],
                "after": step.after.to_dict(),
            }
            store.append("trajectory.jsonl", record)
            trajectory.append(record)
        result: dict[str, object] = {
            "schema": "drone.platform.contract/v0.2",
            "episode_id": episode_id,
            "status": outcome.status.value,
            "success": outcome.status is EpisodeStatus.SUCCESS,
            "step_count": len(outcome.steps),
            "final_snapshot": outcome.final_snapshot.to_dict(),
            "cleanup_errors": list(outcome.cleanup_errors),
            "record_path": "trajectory.jsonl",
            "scenario": scenario.to_dict(),
        }
        if "evaluator" in selected:
            evaluator = create("evaluator")
            try:
                evaluation_input = {**result, "trajectory": trajectory,
                                    "truth": deepcopy(dict(truth)),
                                    "artifact_root": str(store.directory)}
                original = deepcopy(evaluation_input)
                metrics = evaluator.evaluate(evaluation_input)
                if evaluation_input != original:
                    raise ValueError("evaluator modified its input")
                if (not isinstance(metrics, Mapping) or not metrics or
                        any(not isinstance(key, str) or not key or
                            isinstance(value, bool) or not isinstance(value, (int, float)) or
                            not math.isfinite(value) for key, value in metrics.items())):
                    raise ValueError("evaluator must return finite numeric metrics")
                result["metrics"] = dict(metrics)
            finally:
                evaluator.close()
        store.finish(result)
        active[0] = False
        return result
    except BaseException as exc:
        active[0] = False
        if not run_started:
            lifecycle.close(((name, component) for name, component in
                             (("backend.close", backend), ("task.close", task),
                              *((f"agent:{name}.close", agent) for name, agent in agents.items()),
                              ("scenario.close", scenario_item),
                              ("generator.close", generator if generator is not scenario_item else None))
                             if component is not None and getattr(component, "close", None)),
                            runtime=runtime_item)
            lifecycle.end()
        try:
            lifecycle.emit("runner", "component_error", stage="run_multi",
                           error=f"{type(exc).__name__}: {exc}")
        except (OSError, StoreError):
            pass
        raise


def run_benchmark(data: Mapping[str, Any], output: Path,
                  *, registry: PluginRegistry | None = None,
                  enable_backend: bool = False) -> dict[str, object]:
    registry = registry or discover_plugins()
    benchmark_id, benchmark_config = _selection(data.get("benchmark"), "benchmark")
    registry.resolve(benchmark_id, "benchmark", benchmark_config)
    processor_selection = (None if "processor" not in data else
                           _selection(data["processor"], "processor"))
    if processor_selection is not None:
        registry.resolve(processor_selection[0], "result_processor", processor_selection[1])
    base = deepcopy(dict(data))
    base.pop("benchmark", None)
    base.pop("processor", None)
    base.pop("benchmark_id", None)
    base.pop("episode_id", None)
    selected = _multi_selections(base)
    benchmark_run_id = data.get("benchmark_id") or uuid4().hex
    if not isinstance(benchmark_run_id, str):
        raise ValueError("benchmark_id must be a string")
    output = Path(output)
    store = EpisodeStore(output / "benchmarks")
    store.start(benchmark_run_id, metadata={
        "plugin_api": "drone.plugin.api/v0.2",
        "benchmark": registry.resolve(benchmark_id).to_dict(),
        "benchmark_config": registry.recordable_config(benchmark_id, benchmark_config),
        "components": {role: registry.resolve(identifier).to_dict()
                       for role, (identifier, _) in selected.items()},
    })
    active = [True]
    context = SessionContext(benchmark_run_id, store,
                             lambda kind, fields: store.append("events.jsonl", {
                                 "source": "benchmark", "name": kind, "payload": dict(fields)}),
                             lambda: active[0])
    stage = "benchmark.instantiate"
    active_case_id = None
    try:
        benchmark = registry.instantiate(benchmark_id, benchmark_config,
                                         expected_type="benchmark", context=context)
        stage = "benchmark.cases"
        try:
            cases = benchmark.cases()
        finally:
            benchmark.close()
        if not isinstance(cases, Sequence) or isinstance(cases, (str, bytes)) or not cases:
            raise ValueError("benchmark must return a nonempty case sequence")
        prepared = []
        case_ids = set()
        stage = "benchmark.preflight"
        for index, case in enumerate(cases):
            active_case_id = None
            if not isinstance(case, Mapping) or not isinstance(case.get("case_id"), str):
                raise ValueError("benchmark case requires a case_id")
            case_id = case["case_id"]
            if not case_id or case_id in case_ids:
                raise ValueError("benchmark case IDs must be nonempty and unique")
            active_case_id = case_id
            case_ids.add(case_id)
            if set(case) - {"case_id", "scenario_seed", "seed", "task", "components"}:
                raise ValueError("benchmark case has unsupported fields")
            run_data = deepcopy(base)
            overrides = case.get("components", {})
            if not isinstance(overrides, Mapping) or set(overrides) - {
                    "scenario", "generator", "task", "evaluator"}:
                raise ValueError("benchmark case has invalid component overrides")
            if "scenario" in overrides and "generator" in overrides:
                raise ValueError("benchmark case must select one scenario source")
            if "scenario" in overrides:
                run_data["components"].pop("generator", None)
                run_data.pop("seed", None)
            if "generator" in overrides:
                run_data["components"].pop("scenario", None)
            run_data["components"].update(deepcopy(dict(overrides)))
            if "task" in case:
                run_data["components"]["task"] = {"id": case["task"]}
            if "scenario_seed" in case and "seed" in case:
                raise ValueError("benchmark case must use one seed field")
            if "scenario_seed" in case or "seed" in case:
                run_data["seed"] = case.get("seed", case.get("scenario_seed"))
                if "generator" not in run_data["components"]:
                    raise ValueError("benchmark case seed requires a generator")
            if "generator" in run_data["components"]:
                if (isinstance(run_data.get("seed"), bool) or
                        not isinstance(run_data.get("seed"), int) or run_data["seed"] < 0):
                    raise ValueError("benchmark case seed must be a nonnegative integer")
            run_data["episode_id"] = f"{benchmark_run_id}-{index}"
            _preflight(registry, run_data, enable_backend=enable_backend)
            prepared.append((case_id, run_data))
        entries = []
        episode_ids = []
        for case_id, run_data in prepared:
            stage = "benchmark.run_case"
            active_case_id = case_id
            result = run_multi(run_data, output, registry=registry,
                               enable_backend=enable_backend)
            episode_ids.append(result["episode_id"])
            entries.append({"case_id": case_id, "episode_id": result["episode_id"],
                            "seed": result["scenario"]["seed"],
                            "status": result["status"], "success": result["success"],
                            "metrics": result.get("metrics", {})})
        active_case_id = None
        summary: dict[str, object] = {
            "episode_id": benchmark_run_id,
            "benchmark": benchmark_id,
            "case_count": len(entries),
            "success_count": sum(item["success"] is True for item in entries),
            "cases": entries,
        }
        if processor_selection is not None:
            identifier, config = processor_selection
            stage = "processor.instantiate"
            processor = registry.instantiate(identifier, config,
                                             expected_type="result_processor", context=context)
            try:
                stage = "processor.process"
                processed = processor.process(
                    lambda episode_id: EpisodeStore.read_result(output, episode_id),
                    tuple(episode_ids))
                if not isinstance(processed, Mapping):
                    raise ValueError("result processor must return a mapping")
                summary["processed"] = dict(processed)
            finally:
                processor.close()
        stage = "benchmark.finish"
        store.finish(summary)
        return summary
    except BaseException as exc:
        try:
            context.emit("component_error", {
                "stage": stage, "case_id": active_case_id,
                "error": f"{type(exc).__name__}: {exc}",
            })
        except (OSError, StoreError):
            pass
        raise
    finally:
        active[0] = False


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
    benchmark = sub.add_parser("run-benchmark", help="run and score a v0.2 benchmark case set")
    benchmark.add_argument("config", type=Path)
    benchmark.add_argument("--output", type=Path, default=Path("runs"))
    benchmark.add_argument("--manifest", action="append", type=Path, default=[])
    benchmark.add_argument("--enable-backend", action="store_true")
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
        if args.command == "run-benchmark":
            result = run_benchmark(data, args.output, registry=registry,
                                   enable_backend=args.enable_backend)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        result = run_multi(data, args.output, registry=registry,
                           enable_backend=args.enable_backend)
        print(json.dumps(result, ensure_ascii=False))
        return int(not result["success"])
    except (OSError, ValueError, PluginRegistryError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
