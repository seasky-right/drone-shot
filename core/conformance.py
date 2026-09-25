"""Python entry point for candidate v0.2 plugin conformance checks."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping
from uuid import uuid4

from contracts.data_v02 import PlatformObservationV02
from contracts.model import ContractValidationError
from contracts.plugin_v02 import PLUGIN_API_VERSION, PluginType

from .plugins import PluginRegistry, PluginRegistryError
from .conformance_probes import ProbeFailure, exercise_component
from .session import SessionContext
from .store import EpisodeStore, _component


def declared_type_coverage(registry: PluginRegistry) -> dict[str, list[str]]:
    """List every public component category, including absent categories."""
    if registry.issues:
        raise PluginRegistryError(registry.issues)
    coverage = {kind.value: [] for kind in PluginType}
    for descriptor in registry.list():
        coverage[descriptor.type].append(descriptor.id)
    return coverage


def validate_component_cases(
    registry: PluginRegistry,
    cases: Mapping[str, Mapping[str, Any]],
    *,
    enable_backend: bool = False,
) -> dict[str, dict[str, object]]:
    """Run selected component hooks and report each declared component separately.

    Each case accepts {"config": {...}, "probe": {...}}. A bare config mapping
    remains accepted for the earlier candidate API. Unselected components are
    unchecked; successful construction alone is never a passing result.
    """
    declared_type_coverage(registry)
    if not isinstance(cases, Mapping):
        raise ValueError("component cases must map component IDs to configurations")
    results: dict[str, dict[str, object]] = {}
    for item in registry.list():
        component_api = item.capabilities.get("component_api", PLUGIN_API_VERSION)
        if component_api != PLUGIN_API_VERSION:
            results[item.id] = {
                "type": item.type, "status": "unsupported", "stage": "contract",
                "checks": [], "reason": f"component uses {component_api}; v0.2 probe is unavailable",
            }
        else:
            results[item.id] = {
                "type": item.type, "status": "unchecked", "checks": [],
                "reason": "no executable case supplied",
            }
    with TemporaryDirectory(prefix="drone-plugin-factories-") as temporary:
        store = EpisodeStore(Path(temporary))
        store.start("factory-cases")
        active = [True]
        context = SessionContext(
            "factory-cases", store,
            lambda kind, fields: store.append("events.jsonl", {"kind": kind, "fields": dict(fields)}),
            lambda: active[0],
        )
        try:
            for component_id, case in sorted(cases.items()):
                if not isinstance(case, Mapping):
                    results[component_id] = {
                        "type": None, "status": "failed", "stage": "case",
                        "checks": [], "reason": "case must be an object",
                    }
                    continue
                structured = "config" in case or "probe" in case
                config = case.get("config", {}) if structured else case
                probe = case.get("probe", {}) if structured else {}
                if not isinstance(config, Mapping) or not isinstance(probe, Mapping):
                    results[component_id] = {
                        "type": None, "status": "failed", "stage": "case",
                        "checks": [], "reason": "config and probe must be objects",
                    }
                    continue
                try:
                    descriptor = registry.resolve(component_id)
                except PluginRegistryError:
                    results[component_id] = {
                        "type": None, "status": "failed", "stage": "selection",
                        "checks": [], "reason": "component ID is not registered",
                    }
                    continue
                component_api = descriptor.capabilities.get(
                    "component_api", PLUGIN_API_VERSION)
                if component_api != PLUGIN_API_VERSION:
                    continue
                report: dict[str, object] = {
                    "type": descriptor.type, "status": "failed", "checks": [],
                }
                results[component_id] = report
                if (descriptor.type == PluginType.BACKEND.value
                        and descriptor.capabilities.get("requires_explicit_enable")
                        and not enable_backend):
                    report.update(status="unsupported", stage="preflight",
                                  reason="backend requires --enable-backend")
                    continue
                component = None
                stage = "config"
                try:
                    registry.resolve(component_id, descriptor.type, config)
                    stage = "factory"
                    component = registry.instantiate(
                        component_id, config, expected_type=descriptor.type,
                        context=context)
                    if not callable(getattr(component, "close", None)):
                        raise ProbeFailure("hooks", "component.close is missing")
                    stage = "probe"
                    checks = exercise_component(
                        descriptor.type, component, descriptor.capabilities, probe)
                    report.update(status="passed", checks=checks)
                except ProbeFailure as exc:
                    report.update(stage=exc.stage, reason=str(exc))
                except PluginRegistryError as exc:
                    report.update(stage=stage, reason=exc.issues[0].message)
                except Exception as exc:
                    report.update(stage=stage,
                                  reason=f"{type(exc).__name__} during {stage}")
                finally:
                    if component is not None:
                        close = getattr(component, "close", None)
                        if callable(close):
                            try:
                                close()
                                if report["status"] == "passed":
                                    report["checks"].append("close")
                            except Exception as exc:
                                report["cleanup_error"] = type(exc).__name__
                                report["status"] = "failed"
                                report.setdefault("stage", "close")
                                report.setdefault("reason", "component.close failed")
        finally:
            active[0] = False
    return results


def validate_plugin(
    registry: PluginRegistry,
    *,
    sample: Mapping[str, Any] | None = None,
    output: str | Path = "runs",
    component_cases: Mapping[str, Mapping[str, Any]] | None = None,
    enable_backend: bool = False,
) -> dict[str, object]:
    """Validate static declarations and any explicitly supplied runtime cases."""
    coverage = declared_type_coverage(registry)
    component_results = validate_component_cases(
        registry, component_cases or {}, enable_backend=enable_backend)
    failed = sorted(identifier for identifier, item in component_results.items()
                    if item["status"] == "failed")
    unchecked = sorted(identifier for identifier, item in component_results.items()
                       if item["status"] == "unchecked")
    unsupported = sorted(identifier for identifier, item in component_results.items()
                         if item["status"] == "unsupported")
    selected = set(component_cases or {})
    report: dict[str, object] = {
        "manifest_valid": True,
        "declared_types": coverage,
        "component_count": sum(map(len, coverage.values())),
        "component_results": component_results,
        "checked_components": sorted(identifier for identifier, item in
                                     component_results.items()
                                     if item["status"] == "passed"),
        "unchecked_components": unchecked,
        "unsupported_components": unsupported,
        "complete": not (failed or unchecked or unsupported),
        "status": "failed" if failed else
                  "partial" if unchecked or unsupported else "passed",
        "valid": bool(selected) and not failed and not (selected & set(unsupported)),
        "runtime_checked": False,
    }
    if sample is None:
        return report

    # Import after module initialization; the CLI also exposes the run service.
    from .plugin_cli import _runtime_negative_checks, run_multi

    supplied = dict(sample)
    sample_id = supplied.get("episode_id")
    destination = Path(output)
    if isinstance(sample_id, str) and (destination / _component(sample_id)).exists():
        supplied["episode_id"] = f"validation-{uuid4().hex}"
    try:
        result = run_multi(supplied, destination, registry=registry,
                           enable_backend=enable_backend)
        EpisodeStore.read_result(destination, result["episode_id"])
        checks, skipped = _runtime_negative_checks(supplied, registry)
    except Exception as exc:
        report.update(valid=False, status="failed",
                      sample_error=f"{type(exc).__name__}: {exc}")
        return report
    checks["result_reread"] = True
    observations = result["final_snapshot"]["observations"]
    if observations:
        public = dict(next(iter(observations.values())))
        public["ground_truth"] = {"forbidden": True}
        try:
            PlatformObservationV02.from_dict(public)
        except ContractValidationError:
            checks["truth_field_rejection"] = True
        else:
            raise ValueError("Agent observation accepted a ground-truth field")
    report.update(valid=bool(result["success"]) and not failed and
                  not (selected & set(unsupported)), runtime_checked=True,
                  checks=checks, skipped_checks=skipped, sample=result)
    if not result["success"]:
        report["status"] = "failed"
    return report
