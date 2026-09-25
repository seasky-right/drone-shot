"""Static plugin discovery and preflight for the candidate v0.2 API.

Installed packs advertise a ``drone.plugins.v02`` entry point and include a
``drone_plugin.json`` file beside the entry point's top-level package, or at
the distribution root, in their wheel RECORD. Discovery reads that file
through distribution metadata; only ``instantiate`` imports plugin code.
"""

from __future__ import annotations

import importlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from contracts.plugin_v02 import PLUGIN_API_VERSION


ENTRY_POINT_GROUP = "drone.plugins.v02"
MANIFEST_RESOURCE = "drone_plugin.json"
_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*(?:/[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*)*$")
_NAMESPACED = re.compile(r"^[a-z][a-z0-9_.-]*/[a-z][a-z0-9_.-]*$")
_IMPORT = re.compile(r"^[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*:[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*$")
_TYPES = frozenset({"backend", "task", "agent", "evaluator", "scenario",
                    "scenario_generator", "runtime_provider", "training_driver",
                    "result_processor", "benchmark"})
_FORMAT_CHECKER = FormatChecker()


@_FORMAT_CHECKER.checks("episode-relative-path")
def _episode_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value:
        return False
    parts = value.split("/")
    return (not PurePosixPath(value).is_absolute()
            and all(part not in ("", ".", "..") for part in parts))


@dataclass(frozen=True)
class RegistryIssue:
    code: str
    source: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "source": self.source, "message": self.message,
                "details": dict(self.details)}


class PluginRegistryError(ValueError):
    def __init__(self, issues: tuple[RegistryIssue, ...] | list[RegistryIssue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{item.code}: {item.message}" for item in self.issues))


@dataclass(frozen=True)
class PluginDependency:
    id: str
    version: str


@dataclass(frozen=True)
class ComponentDescriptor:
    id: str
    type: str
    pack_id: str
    version: str
    plugin_api: str
    entry_point: str
    dependencies: tuple[PluginDependency, ...]
    capabilities: Mapping[str, Any]
    requires: Mapping[str, Any]
    config_schema: Mapping[str, Any]
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "pack_id": self.pack_id,
                "version": self.version, "plugin_api": self.plugin_api,
                "entry_point": self.entry_point,
                "dependencies": [vars(dep) for dep in self.dependencies],
                "capabilities": dict(self.capabilities), "requires": dict(self.requires),
                "source": self.source}


@dataclass(frozen=True)
class PreflightReport:
    ordered: tuple[ComponentDescriptor, ...]
    issues: tuple[RegistryIssue, ...]
    effective_config: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    recordable_config: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "ordered": [item.to_dict() for item in self.ordered],
                "issues": [item.to_dict() for item in self.issues],
                "recordable_config": {key: dict(value) for key, value in
                                      sorted(self.recordable_config.items())}}


def _issue(code: str, source: str, message: str, **details: Any) -> RegistryIssue:
    return RegistryIssue(code, source, message, details)


def _require_object(value: Any, source: str, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise PluginRegistryError([_issue("invalid_manifest", source, f"{label} must be an object")])
    return value


def _fields(value: Mapping[str, Any], required: set[str], optional: set[str], source: str,
            label: str) -> None:
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing or extra:
        raise PluginRegistryError([_issue("invalid_manifest", source, f"{label} has missing or unknown fields",
                                          missing=sorted(missing), unknown=sorted(extra))])


def _schema(value: Any, source: str, label: str) -> Mapping[str, Any]:
    schema = _require_object(value, source, label)
    # Network or file references make preflight depend on untrusted external state.
    visited: set[int] = set()
    def check_refs(node: Any) -> None:
        if not isinstance(node, dict) or id(node) in visited:
            return
        visited.add(id(node))
        for key in ("$dynamicRef", "$recursiveRef"):
            if key in node:
                raise PluginRegistryError([_issue("invalid_schema", source,
                                                  f"{label} does not support {key}")])
        reference = node.get("$ref")
        if reference is not None:
            target = _local_ref(schema, reference) if isinstance(reference, str) else None
            if target is None:
                raise PluginRegistryError([_issue("invalid_schema", source,
                                                  f"{label} contains an external or unresolved reference")])
            check_refs(target)
        for key in ("items", "contains", "if", "then", "else", "not",
                    "additionalProperties", "unevaluatedProperties", "propertyNames",
                    "unevaluatedItems", "contentSchema"):
            check_refs(node.get(key))
        for key in ("properties", "patternProperties", "$defs", "definitions",
                    "dependentSchemas"):
            children = node.get(key)
            if isinstance(children, dict):
                for child in children.values():
                    check_refs(child)
        for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
            children = node.get(key)
            if isinstance(children, list):
                for child in children:
                    check_refs(child)
    check_refs(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise PluginRegistryError([_issue("invalid_schema", source, f"{label}: {exc.message}")]) from exc
    return schema


def _local_ref(root: Mapping[str, Any], reference: str) -> Any | None:
    if reference == "#":
        return root
    if not reference.startswith("#/"):
        return None
    target: Any = root
    for token in reference[2:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(target, dict):
            if key not in target:
                return None
            target = target[key]
        elif isinstance(target, list):
            if not key.isdecimal() or int(key) >= len(target):
                return None
            target = target[int(key)]
        else:
            return None
    return target


def _capabilities(value: Any, source: str, label: str) -> Mapping[str, Any]:
    capability = _require_object(value, source, label)
    try:
        json.dumps(capability, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PluginRegistryError([_issue("invalid_capability", source,
                                          f"{label} must be JSON-safe")]) from exc
    namespaced = ("action_kinds", "sensor_types")
    simple_lists = ("time_bases", "scenario_operations")
    for key in (*namespaced, *simple_lists):
        if key not in capability:
            continue
        items = capability[key]
        if (not isinstance(items, list)
                or any(not isinstance(item, str) or not item for item in items)
                or len(items) != len(set(items))
                or (key in namespaced and any(not _NAMESPACED.fullmatch(item) for item in items))):
            raise PluginRegistryError([_issue("invalid_capability", source,
                                              f"{label}.{key} must be a unique string array")])
    if "sensor_resources" in capability:
        resources = capability["sensor_resources"]
        if (not isinstance(resources, dict)
                or any(not isinstance(key, str) or not key or not isinstance(kind, str)
                       or not _NAMESPACED.fullmatch(kind) for key, kind in resources.items())):
            raise PluginRegistryError([_issue("invalid_capability", source,
                                              f"{label}.sensor_resources must map IDs to sensor kinds")])
    if "max_vehicles" in capability and (isinstance(capability["max_vehicles"], bool)
                                          or not isinstance(capability["max_vehicles"], int)
                                          or capability["max_vehicles"] < 1):
        raise PluginRegistryError([_issue("invalid_capability", source,
                                          f"{label}.max_vehicles must be positive")])
    if "coordinate_frame" in capability and (not isinstance(capability["coordinate_frame"], str)
                                             or not capability["coordinate_frame"]):
        raise PluginRegistryError([_issue("invalid_capability", source,
                                          f"{label}.coordinate_frame must be nonempty text")])
    if "scenario_id" in capability and (not isinstance(capability["scenario_id"], str)
                                         or not capability["scenario_id"]):
        raise PluginRegistryError([_issue("invalid_capability", source,
                                          f"{label}.scenario_id must be nonempty text")])
    if "component_api" in capability and capability["component_api"] not in (
            "drone.plugin.api/v0.1", PLUGIN_API_VERSION):
        raise PluginRegistryError([_issue("invalid_capability", source,
                                          f"{label}.component_api must be a supported component API version")])
    for key in ("truth_access", "bounded_execution", "interruptible", "isolated",
                "requires_explicit_enable"):
        if key in capability and not isinstance(capability[key], bool):
            raise PluginRegistryError([_issue("invalid_capability", source,
                                              f"{label}.{key} must be boolean")])
    return capability


def _validate_requirements(value: Any, source: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise PluginRegistryError([_issue("invalid_capability_requirement", source,
                                          "capability requirements must be an object")])
    required = value
    allowed = {"action_kinds", "sensor_types", "sensor_resources", "coordinate_frame",
               "time_bases", "min_vehicles", "scenario_operations", "truth_access",
               "bounded_execution", "hard_cancel", "scenario_id"}
    unknown = sorted(required.keys() - allowed)
    if unknown:
        raise PluginRegistryError([_issue("invalid_capability_requirement", source,
                                          "unknown capability requirement", unknown=unknown)])
    try:
        _capabilities({key: value for key, value in required.items()
                       if key not in ("min_vehicles", "hard_cancel")},
                      source, "capability requirements")
    except PluginRegistryError as exc:
        raise PluginRegistryError([_issue("invalid_capability_requirement", source,
                                          issue.message) for issue in exc.issues]) from exc
    vehicles = required.get("min_vehicles")
    if vehicles is not None and (isinstance(vehicles, bool) or not isinstance(vehicles, int)
                                 or vehicles < 1):
        raise PluginRegistryError([_issue("invalid_capability_requirement", source,
                                          "min_vehicles must be positive")])
    hard_cancel = required.get("hard_cancel", False)
    if not isinstance(hard_cancel, bool):
        raise PluginRegistryError([_issue("invalid_capability_requirement", source,
                                          "hard_cancel must be boolean")])
    return required


def _capability_issues(component: ComponentDescriptor,
                       requirements: Mapping[str, Any]) -> tuple[RegistryIssue, ...]:
    source = component.source
    required = _validate_requirements(requirements, source)
    vehicles = required.get("min_vehicles")
    hard_cancel = required.get("hard_cancel", False)
    declared = component.capabilities
    issues: list[RegistryIssue] = []
    for key in ("action_kinds", "sensor_types", "time_bases", "scenario_operations"):
        missing = sorted(set(required.get(key, ())) - set(declared.get(key, ())))
        if missing:
            issues.append(_issue("insufficient_capability", source,
                                 f"{component.id} does not declare required {key}",
                                 field=key, missing=missing))
    for resource_id, kind in required.get("sensor_resources", {}).items():
        if declared.get("sensor_resources", {}).get(resource_id) != kind:
            issues.append(_issue("insufficient_capability", source,
                                 f"{component.id} does not declare sensor resource {resource_id}",
                                 field="sensor_resources", resource_id=resource_id))
    for key in ("coordinate_frame", "scenario_id"):
        if key in required and declared.get(key) != required[key]:
            issues.append(_issue("insufficient_capability", source,
                                 f"{component.id} does not declare required {key}", field=key))
    if vehicles is not None and declared.get("max_vehicles", 0) < vehicles:
        issues.append(_issue("insufficient_capability", source,
                             f"{component.id} declares insufficient vehicle capacity",
                             field="max_vehicles", required=vehicles,
                             declared=declared.get("max_vehicles")))
    for key in ("truth_access", "bounded_execution"):
        if required.get(key) is True and declared.get(key) is not True:
            issues.append(_issue("insufficient_capability", source,
                                 f"{component.id} does not declare {key}", field=key))
    if hard_cancel:
        issues.append(_issue(
            "hard_cancel_unverified", source,
            "hard cancellation has no runtime proof interface; declarations are insufficient",
            field="hard_cancel"))
    return tuple(issues)


def _with_defaults(value: Any, schema: Mapping[str, Any], root: Mapping[str, Any],
                   active_refs: frozenset[str] = frozenset()) -> Any:
    """Apply unambiguous property/item defaults to a detached JSON value."""
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference not in active_refs:
        target = _local_ref(root, reference)
        if isinstance(target, dict):
            value = _with_defaults(value, target, root, active_refs | {reference})
    for subschema in schema.get("allOf", []):
        if isinstance(subschema, dict):
            value = _with_defaults(value, subschema, root, active_refs)
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key in sorted(properties):
                child_schema = properties[key]
                if not isinstance(child_schema, dict):
                    continue
                if key not in value and "default" in child_schema:
                    value[key] = deepcopy(child_schema["default"])
                if key in value:
                    value[key] = _with_defaults(value[key], child_schema, root, active_refs)
    elif isinstance(value, list):
        prefix = schema.get("prefixItems", [])
        if isinstance(prefix, list):
            for index, child_schema in enumerate(prefix[:len(value)]):
                if isinstance(child_schema, dict):
                    value[index] = _with_defaults(value[index], child_schema, root, active_refs)
        items = schema.get("items")
        if isinstance(items, dict):
            for index in range(len(prefix) if isinstance(prefix, list) else 0, len(value)):
                value[index] = _with_defaults(value[index], items, root, active_refs)
    return value


def _redact(value: Any, schema: Mapping[str, Any], root: Mapping[str, Any],
            active_refs: frozenset[str] = frozenset()) -> Any:
    if schema.get("writeOnly") is True or schema.get("x-secret") is True:
        return "[REDACTED]"
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference not in active_refs:
        target = _local_ref(root, reference)
        if isinstance(target, dict):
            value = _redact(value, target, root, active_refs | {reference})
    # Redact across every branch. Extra redaction is preferable to recording a
    # secret when the instance matches several schema branches.
    for branch in ("allOf", "anyOf", "oneOf"):
        for subschema in schema.get(branch, []):
            if isinstance(subschema, dict):
                value = _redact(value, subschema, root, active_refs)
    for branch in ("if", "then", "else", "not"):
        subschema = schema.get(branch)
        if isinstance(subschema, dict):
            value = _redact(value, subschema, root, active_refs)
    dependent = schema.get("dependentSchemas", {})
    if isinstance(dependent, dict):
        for subschema in dependent.values():
            if isinstance(subschema, dict):
                value = _redact(value, subschema, root, active_refs)
    if isinstance(value, dict):
        result = deepcopy(value)
        properties = schema.get("properties", {})
        patterns = schema.get("patternProperties", {})
        additional = schema.get("additionalProperties")
        for key in result:
            child_schema = properties.get(key, additional) if isinstance(properties, dict) else additional
            if isinstance(child_schema, dict):
                result[key] = _redact(result[key], child_schema, root, active_refs)
            if isinstance(patterns, dict):
                for pattern, pattern_schema in patterns.items():
                    if re.search(pattern, key) and isinstance(pattern_schema, dict):
                        result[key] = _redact(result[key], pattern_schema, root, active_refs)
            unevaluated = schema.get("unevaluatedProperties")
            if isinstance(unevaluated, dict):
                result[key] = _redact(result[key], unevaluated, root, active_refs)
        return result
    if isinstance(value, list):
        result = deepcopy(value)
        prefix = schema.get("prefixItems", [])
        for index in range(len(result)):
            child_schema = (prefix[index] if isinstance(prefix, list) and index < len(prefix)
                            else schema.get("items"))
            if isinstance(child_schema, dict):
                result[index] = _redact(result[index], child_schema, root, active_refs)
            contains = schema.get("contains")
            if isinstance(contains, dict):
                result[index] = _redact(result[index], contains, root, active_refs)
        return result
    return value


def _recordable(component: ComponentDescriptor, effective: Mapping[str, Any]) -> dict[str, Any]:
    redacted = _redact(effective, component.config_schema, component.config_schema)
    return redacted if isinstance(redacted, dict) else {}


def _identifier(value: Any, source: str, label: str, *, component: bool = False) -> str:
    if (not isinstance(value, str) or not _ID.fullmatch(value)
            or (component and "/" not in value)
            or (label == "pack id" and "/" in value)):
        raise PluginRegistryError([_issue("invalid_id", source, f"{label} must be a stable lowercase ID")])
    return value


def _entry_point(value: Any, source: str, label: str) -> str:
    if not isinstance(value, str) or not _IMPORT.fullmatch(value):
        raise PluginRegistryError([_issue("invalid_manifest", source,
                                          f"{label} must be a module:factory reference")])
    return value


def _version(value: Any, source: str) -> str:
    try:
        if not isinstance(value, str):
            raise InvalidVersion(str(value))
        Version(value)
    except InvalidVersion as exc:
        raise PluginRegistryError([_issue("invalid_version", source, "invalid pack version")]) from exc
    return value


def _dependencies(value: Any, source: str) -> tuple[PluginDependency, ...]:
    if not isinstance(value, list):
        raise PluginRegistryError([_issue("invalid_manifest", source, "dependencies must be an array")])
    result: list[PluginDependency] = []
    seen: set[str] = set()
    for row in value:
        item = _require_object(row, source, "dependency")
        _fields(item, {"id", "version"}, set(), source, "dependency")
        dep_id = _identifier(item["id"], source, "dependency id")
        spec = item["version"]
        try:
            if not isinstance(spec, str):
                raise InvalidSpecifier(str(spec))
            SpecifierSet(spec)
        except InvalidSpecifier as exc:
            raise PluginRegistryError([_issue("invalid_version_range", source,
                                              f"invalid version range for {dep_id}")]) from exc
        if dep_id in seen:
            raise PluginRegistryError([_issue("duplicate_dependency", source,
                                              f"duplicate dependency {dep_id}")])
        seen.add(dep_id)
        result.append(PluginDependency(dep_id, spec))
    return tuple(sorted(result, key=lambda dep: dep.id))


def _parse_manifest(value: Any, source: str, advertised_id: str | None,
                    advertised_entry: str | None) -> tuple[str, str, tuple[ComponentDescriptor, ...]]:
    root = _require_object(value, source, "manifest")
    required = {"id", "version", "type", "plugin_api", "entry_point", "dependencies",
                "capabilities", "config_schema"}
    _fields(root, required, {"components", "requires"}, source, "manifest")
    pack_id = _identifier(root["id"], source, "pack id")
    version = _version(root["version"], source)
    api = root["plugin_api"]
    if api != PLUGIN_API_VERSION:
        raise PluginRegistryError([_issue("incompatible_api", source,
                                          f"{pack_id} requires {api!r}; Core supports {PLUGIN_API_VERSION!r}")])
    entry = _entry_point(root["entry_point"], source, "entry_point")
    if advertised_id is not None and (advertised_id != pack_id or advertised_entry != entry):
        raise PluginRegistryError([_issue("entry_point_mismatch", source,
                                          "distribution entry point does not match manifest")])
    deps = _dependencies(root["dependencies"], source)
    capabilities = _capabilities(root["capabilities"], source, "capabilities")
    root_schema = _schema(root["config_schema"], source, "config_schema")
    kind = root["type"]
    components = root.get("components")
    if kind == "pack":
        if "requires" in root:
            raise PluginRegistryError([_issue("invalid_capability_requirement", source,
                                              "pack cannot declare component requirements")])
        if not isinstance(components, list) or not components:
            raise PluginRegistryError([_issue("invalid_manifest", source,
                                              "pack requires nonempty components")])
    elif kind in _TYPES:
        if components is not None:
            raise PluginRegistryError([_issue("invalid_manifest", source,
                                              "single-component manifest cannot have components")])
        components = [{"id": f"{pack_id}/{kind}", "type": kind, "entry_point": entry,
                       "capabilities": capabilities, "requires": root.get("requires", {}),
                       "config_schema": root_schema}]
    else:
        raise PluginRegistryError([_issue("invalid_type", source, f"unknown component type {kind!r}")])
    result: list[ComponentDescriptor] = []
    for index, row in enumerate(components):
        item = _require_object(row, source, f"components[{index}]")
        fields = {"id", "type", "entry_point", "capabilities", "config_schema"}
        _fields(item, fields, {"requires"}, source, f"components[{index}]")
        comp_id = _identifier(item["id"], source, "component id", component=True)
        if item["type"] not in _TYPES:
            raise PluginRegistryError([_issue("invalid_type", source,
                                              f"unknown component type {item['type']!r}")])
        requires = _validate_requirements(item.get("requires", {}), source)
        if requires and item["type"] not in {"task", "agent", "scenario"}:
            raise PluginRegistryError([_issue("invalid_capability_requirement", source,
                                              "requires is supported only on task, agent, and scenario")])
        result.append(ComponentDescriptor(
            comp_id, item["type"], pack_id, version, api,
            _entry_point(item["entry_point"], source, "component entry_point"), deps,
            _capabilities(item["capabilities"], source, "component capabilities"),
            requires, _schema(item["config_schema"], source, "component config_schema"),
            source))
    return pack_id, version, tuple(result)


class PluginRegistry:
    """Deterministic registry; discovery and preflight never import plugin code."""

    def __init__(self) -> None:
        self._packs: dict[str, tuple[str, str]] = {}
        self._components: dict[str, ComponentDescriptor] = {}
        self._issues: list[RegistryIssue] = []

    @classmethod
    def discover(cls, manifest_paths: tuple[str | Path, ...] = ()) -> "PluginRegistry":
        registry = cls()
        distributions = sorted(metadata.distributions(), key=lambda dist: (
            (dist.metadata.get("Name") or "").lower(), dist.version))
        for dist in distributions:
            entries = sorted((ep for ep in dist.entry_points if ep.group == ENTRY_POINT_GROUP),
                             key=lambda ep: (ep.name, ep.value))
            if not entries:
                continue
            source = f"distribution:{dist.metadata.get('Name', '<unknown>')}=={dist.version}"
            if len(entries) != 1:
                registry._issues.append(_issue("invalid_distribution", source,
                                               "expected exactly one pack entry point"))
                continue
            advertised_entry = entries[0].value
            if not _IMPORT.fullmatch(advertised_entry):
                registry._issues.append(_issue("invalid_distribution", source,
                                               "pack entry point must be a module:factory reference"))
                continue
            top_level = advertised_entry.split(":", 1)[0].split(".", 1)[0]
            candidates = (f"{top_level}/{MANIFEST_RESOURCE}", MANIFEST_RESOURCE)
            files = dist.files
            recorded = set() if files is None else {
                str(item).replace("\\", "/") for item in files}
            resource = next((item for item in candidates if item in recorded), None)
            if resource is None:
                registry._issues.append(_issue("missing_manifest", source,
                                               "plugin manifest is absent from distribution RECORD",
                                               expected=list(candidates)))
                continue
            path = Path(dist.locate_file(resource))
            registry._add_path(path, source, entries[0].name, entries[0].value,
                               dist.version)
        for path in sorted((Path(item) for item in manifest_paths), key=lambda item: str(item)):
            registry._add_path(path, str(path), None, None, None)
        registry._check_dependencies()
        return registry

    def _add_path(self, path: Path, source: str, advertised_id: str | None,
                  advertised_entry: str | None, distribution_version: str | None) -> None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            pack_id, version, components = _parse_manifest(value, source,
                                                             advertised_id, advertised_entry)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self._issues.append(_issue("unreadable_manifest", source, str(exc)))
            return
        except PluginRegistryError as exc:
            self._issues.extend(exc.issues)
            return
        if distribution_version is not None and Version(version) != Version(distribution_version):
            self._issues.append(_issue("distribution_version_mismatch", source,
                                       f"manifest version {version} differs from distribution version "
                                       f"{distribution_version}"))
            return
        if pack_id in self._packs:
            self._issues.append(_issue("duplicate_pack", source, f"duplicate pack ID {pack_id}",
                                       previous_source=self._packs[pack_id][1]))
            return
        self._packs[pack_id] = (version, source)
        for component in components:
            if component.id in self._components:
                self._issues.append(_issue("duplicate_component", source,
                                           f"duplicate component ID {component.id}",
                                           previous_source=self._components[component.id].source))
            else:
                self._components[component.id] = component

    def _check_dependencies(self) -> None:
        edges: dict[str, set[str]] = {pack: set() for pack in self._packs}
        representatives = {component.pack_id: component for component in self._components.values()}
        for component in representatives.values():
            for dep in component.dependencies:
                target = self._packs.get(dep.id)
                if target is None:
                    self._issues.append(_issue("missing_dependency", component.source,
                                               f"{component.pack_id} requires missing pack {dep.id}"))
                elif Version(target[0]) not in SpecifierSet(dep.version):
                    self._issues.append(_issue("incompatible_dependency", component.source,
                                               f"{dep.id} version {target[0]} does not satisfy {dep.version}"))
                else:
                    edges[component.pack_id].add(dep.id)
        visited: set[str] = set()
        active: set[str] = set()
        def visit(pack: str) -> None:
            if pack in active:
                self._issues.append(_issue("dependency_cycle", self._packs[pack][1],
                                           f"dependency cycle includes {pack}"))
                return
            if pack in visited:
                return
            active.add(pack)
            for dep in sorted(edges[pack]):
                visit(dep)
            active.remove(pack)
            visited.add(pack)
        for pack in sorted(edges):
            visit(pack)

    def list(self) -> tuple[ComponentDescriptor, ...]:
        return tuple(self._components[key] for key in sorted(self._components))

    @property
    def issues(self) -> tuple[RegistryIssue, ...]:
        return tuple(self._issues)

    def resolve(self, component_id: str, expected_type: str | None = None,
                config: Mapping[str, Any] | None = None,
                requirements: Mapping[str, Any] | None = None) -> ComponentDescriptor:
        if self._issues:
            raise PluginRegistryError(self.issues)
        component = self._components.get(component_id)
        if component is None:
            raise PluginRegistryError([_issue("unknown_component", component_id,
                                              f"unknown component {component_id}")])
        if expected_type is not None and component.type != expected_type:
            raise PluginRegistryError([_issue("wrong_component_type", component.source,
                                              f"{component_id} is {component.type}, expected {expected_type}")])
        self._validate_config(component, config)
        if requirements is not None:
            issues = _capability_issues(component, requirements)
            if issues:
                raise PluginRegistryError(issues)
        return component

    @staticmethod
    def _validate_config(component: ComponentDescriptor,
                         config: Mapping[str, Any] | None) -> dict[str, Any]:
        value = deepcopy(component.config_schema.get("default", {})) if config is None else config
        try:
            value = json.loads(json.dumps(value, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise PluginRegistryError([_issue("invalid_config", component.source,
                                              f"{component.id} config must be JSON-safe")]) from exc
        if not isinstance(value, dict):
            raise PluginRegistryError([_issue("invalid_config", component.source,
                                              f"{component.id} config must be an object")])
        value = _with_defaults(value, component.config_schema, component.config_schema)
        try:
            value = json.loads(json.dumps(value, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise PluginRegistryError([_issue("invalid_config", component.source,
                                              f"{component.id} schema default is not JSON-safe")]) from exc
        try:
            Draft202012Validator(component.config_schema,
                                 format_checker=_FORMAT_CHECKER).validate(value)
        except ValidationError as exc:
            raise PluginRegistryError([_issue("invalid_config", component.source,
                                              f"{component.id} config failed {exc.validator} validation",
                                              path=list(exc.absolute_path),
                                              schema_path=list(exc.absolute_schema_path))]) from exc
        return value

    def effective_config(self, component_id: str,
                         config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Return validated defaults for recording; never mutate caller data."""
        component = self.resolve(component_id, config=config)
        return self._validate_config(component, config)

    def recordable_config(self, component_id: str,
                          config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Return effective config with schema-marked secrets removed."""
        component = self.resolve(component_id, config=config)
        effective = self._validate_config(component, config)
        return _recordable(component, effective)

    def preflight(self, selections: Mapping[str, Mapping[str, Any] | None], *,
                  requirements: Mapping[str, Mapping[str, Any]] | None = None) -> PreflightReport:
        issues = list(self._issues)
        selected: dict[str, ComponentDescriptor] = {}
        effective: dict[str, dict[str, Any]] = {}
        recordable: dict[str, dict[str, Any]] = {}
        requirements = {} if requirements is None else requirements
        if not isinstance(requirements, Mapping):
            return PreflightReport((), tuple(issues + [_issue(
                "invalid_capability_requirement", "registry",
                "requirements must map component IDs to capability objects")]))
        for component_id in sorted(requirements.keys() - selections.keys()):
            issues.append(_issue("invalid_capability_requirement", component_id,
                                 "requirements refer to an unselected component"))
        for component_id in sorted(selections):
            component = self._components.get(component_id)
            if component is None:
                issues.append(_issue("unknown_component", component_id,
                                     f"unknown component {component_id}"))
                continue
            try:
                effective[component_id] = self._validate_config(component, selections[component_id])
                recordable[component_id] = _recordable(component, effective[component_id])
            except PluginRegistryError as exc:
                issues.extend(exc.issues)
            if component_id in requirements:
                try:
                    issues.extend(_capability_issues(component, requirements[component_id]))
                except PluginRegistryError as exc:
                    issues.extend(exc.issues)
            selected[component_id] = component
        if issues:
            return PreflightReport((), tuple(issues), effective, recordable)
        pack_order: list[str] = []
        visited: set[str] = set()
        def visit(pack: str) -> None:
            if pack in visited:
                return
            visited.add(pack)
            component = next(item for item in self._components.values() if item.pack_id == pack)
            for dep in component.dependencies:
                visit(dep.id)
            pack_order.append(pack)
        for component in selected.values():
            visit(component.pack_id)
        rank = {pack: index for index, pack in enumerate(pack_order)}
        ordered = tuple(sorted(selected.values(), key=lambda item: (rank[item.pack_id], item.id)))
        return PreflightReport(ordered, (), effective, recordable)

    def instantiate(self, component_id: str, config: Mapping[str, Any] | None = None,
                    expected_type: str | None = None, **factory_kwargs: Any) -> Any:
        component = self.resolve(component_id, expected_type, config)
        effective_config = self._validate_config(component, config)
        module_name, name = component.entry_point.split(":", 1)
        try:
            factory: Any = importlib.import_module(module_name)
            for part in name.split("."):
                factory = getattr(factory, part)
            if not callable(factory):
                raise TypeError("entry point is not callable")
            return factory(config=effective_config, **factory_kwargs)
        except Exception as exc:
            raise PluginRegistryError([_issue("factory_error", component.source,
                                              f"cannot instantiate {component_id}: {exc}")]) from exc


__all__ = ["ENTRY_POINT_GROUP", "MANIFEST_RESOURCE", "RegistryIssue",
           "PluginRegistryError", "PluginDependency", "ComponentDescriptor",
           "PreflightReport", "PluginRegistry"]
