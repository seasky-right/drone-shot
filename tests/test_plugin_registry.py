"""Static registry tests require no simulator or installed plugin."""

from __future__ import annotations

import json
import sys
import zipfile
from types import SimpleNamespace

import pytest

from core import plugins


def manifest(pack_id="example.pack", *, dependencies=None, components=None):
    return {
        "id": pack_id,
        "version": "1.2.0",
        "type": "pack",
        "plugin_api": plugins.PLUGIN_API_VERSION,
        "entry_point": "example_pack:register",
        "dependencies": dependencies or [],
        "capabilities": {},
        "config_schema": {"type": "object", "additionalProperties": False},
        "components": components or [{
            "id": f"{pack_id}/backend",
            "type": "backend",
            "entry_point": "example_pack:create_backend",
            "capabilities": {"vehicle_count": 2},
            "config_schema": {"type": "object", "properties": {
                "speed": {"type": "number", "minimum": 0}}, "additionalProperties": False},
        }],
    }


def write_manifest(tmp_path, value, name):
    path = tmp_path / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_source_manifest_preflight_and_factory_import_boundary(tmp_path, monkeypatch):
    path = write_manifest(tmp_path, manifest(), "pack.json")
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    imported = []
    def fake_import(name):
        imported.append(name)
        return SimpleNamespace(create_backend=lambda *, config: ("backend", config))
    monkeypatch.setattr(plugins.importlib, "import_module", fake_import)
    registry = plugins.PluginRegistry.discover((path,))
    assert registry.issues == ()
    assert [item.id for item in registry.list()] == ["example.pack/backend"]
    assert registry.preflight({"example.pack/backend": {"speed": 2}}).ok
    assert imported == []
    assert registry.instantiate("example.pack/backend", {"speed": 2}, "backend") == (
        "backend", {"speed": 2})
    assert imported == ["example_pack"]


def test_single_component_manifest_uses_stable_pack_type_id(tmp_path, monkeypatch):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    value = manifest()
    value.pop("components")
    value["type"] = "backend"
    value["entry_point"] = "example_pack:create_backend"
    path = write_manifest(tmp_path, value, "single.json")
    registry = plugins.PluginRegistry.discover((path,))
    assert registry.issues == ()
    assert [item.id for item in registry.list()] == ["example.pack/backend"]
    assert registry.preflight({"example.pack/backend": {}}).ok
    imported = []
    def fake_import(name):
        imported.append(name)
        return SimpleNamespace(create_backend=lambda *, config: ("backend", config))
    monkeypatch.setattr(plugins.importlib, "import_module", fake_import)
    assert registry.instantiate("example.pack/backend", {}, "backend") == ("backend", {})
    assert imported == ["example_pack"]


def test_installed_distribution_reads_record_resource_without_loading_entry_point(tmp_path, monkeypatch):
    path = write_manifest(tmp_path, manifest(), plugins.MANIFEST_RESOURCE)
    class Distribution:
        metadata = {"Name": "example-pack"}
        version = "1.2.0"
        files = [plugins.MANIFEST_RESOURCE]
        entry_points = [SimpleNamespace(group=plugins.ENTRY_POINT_GROUP,
                                        name="example.pack", value="example_pack:register",
                                        load=lambda: pytest.fail("entry point loaded during discovery"))]
        def locate_file(self, resource):
            assert resource == plugins.MANIFEST_RESOURCE
            return path
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [Distribution()])
    registry = plugins.PluginRegistry.discover()
    assert registry.issues == ()
    assert registry.resolve("example.pack/backend").source.startswith("distribution:")


def test_installed_wheel_package_resource_is_discovered_without_plugin_import(tmp_path, monkeypatch):
    wheel = tmp_path / "example_pack-1.2.0-py3-none-any.whl"
    dist_info = "example_pack-1.2.0.dist-info"
    files = {
        "example_pack/__init__.py": "raise RuntimeError('discovery imported plugin code')\n",
        "example_pack/drone_plugin.json": json.dumps(manifest()),
        f"{dist_info}/METADATA": "Metadata-Version: 2.3\nName: example-pack\nVersion: 1.2.0\n",
        f"{dist_info}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{dist_info}/entry_points.txt": "[drone.plugins.v02]\nexample.pack = example_pack:register\n",
    }
    files[f"{dist_info}/RECORD"] = "".join(f"{name},,\n" for name in files)
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(installed)
    original_distributions = plugins.metadata.distributions
    monkeypatch.setattr(plugins.metadata, "distributions",
                        lambda: original_distributions(path=[str(installed)]))
    registry = plugins.PluginRegistry.discover()
    assert registry.issues == ()
    assert registry.resolve("example.pack/backend").id == "example.pack/backend"
    assert "example_pack" not in sys.modules


def test_installed_distribution_version_must_match_manifest(tmp_path, monkeypatch):
    path = write_manifest(tmp_path, manifest(), plugins.MANIFEST_RESOURCE)
    class Distribution:
        metadata = {"Name": "example-pack"}
        version = "2.0.0"
        files = [plugins.MANIFEST_RESOURCE]
        entry_points = [SimpleNamespace(group=plugins.ENTRY_POINT_GROUP,
                                        name="example.pack", value="example_pack:register")]
        def locate_file(self, resource):
            return path
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [Distribution()])
    registry = plugins.PluginRegistry.discover()
    assert [issue.code for issue in registry.issues] == ["distribution_version_mismatch"]


def test_duplicate_id_and_missing_dependency_report_before_factory(tmp_path, monkeypatch):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    first = write_manifest(tmp_path, manifest(dependencies=[{"id": "absent.pack", "version": ">=1"}]),
                           "first.json")
    second = write_manifest(tmp_path, manifest(), "second.json")
    registry = plugins.PluginRegistry.discover((first, second))
    assert {issue.code for issue in registry.issues} == {"duplicate_pack", "missing_dependency"}
    with pytest.raises(plugins.PluginRegistryError) as exc:
        registry.instantiate("example.pack/backend")
    assert {issue.code for issue in exc.value.issues} == {"duplicate_pack", "missing_dependency"}


def test_dependency_versions_cycles_and_deterministic_order(tmp_path, monkeypatch):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    base = write_manifest(tmp_path, manifest("base.pack"), "base.json")
    client_value = manifest("client.pack", dependencies=[{"id": "base.pack", "version": ">=1,<2"}])
    client = write_manifest(tmp_path, client_value, "client.json")
    registry = plugins.PluginRegistry.discover((client, base))
    report = registry.preflight({"client.pack/backend": {}, "base.pack/backend": {}})
    assert report.ok
    assert [item.id for item in report.ordered] == ["base.pack/backend", "client.pack/backend"]
    client_value["dependencies"][0]["version"] = ">=2"
    incompatible = write_manifest(tmp_path, client_value, "bad-client.json")
    assert "incompatible_dependency" in {issue.code for issue in
                                          plugins.PluginRegistry.discover((base, incompatible)).issues}
    base_value = manifest("base.pack", dependencies=[{"id": "client.pack", "version": ">=1"}])
    cyclic_base = write_manifest(tmp_path, base_value, "cyclic-base.json")
    assert "dependency_cycle" in {issue.code for issue in
                                  plugins.PluginRegistry.discover((cyclic_base, client)).issues}


@pytest.mark.parametrize("mutation,code", [
    (lambda value: value.update(plugin_api="other/v1"), "incompatible_api"),
    (lambda value: value["components"][0].update(type="unknown"), "invalid_type"),
    (lambda value: value["components"][0].update(config_schema={"type": "nonsense"}),
     "invalid_schema"),
    (lambda value: value["components"][0].update(config_schema={"$ref": "https://example.com/schema"}),
     "invalid_schema"),
    (lambda value: value["components"][0].update(config_schema={"$ref": "#/$defs/missing"}),
     "invalid_schema"),
    (lambda value: value["components"][0].update(config_schema={"$dynamicRef": "#/$defs/item"}),
     "invalid_schema"),
])
def test_manifest_validation(tmp_path, monkeypatch, mutation, code):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    value = manifest()
    mutation(value)
    path = write_manifest(tmp_path, value, "bad.json")
    assert code in {issue.code for issue in plugins.PluginRegistry.discover((path,)).issues}


@pytest.mark.parametrize("requires", [
    {"sensor_types": ["rgb"]},
    {"sensor_resources": {"front": "rgb"}},
    {"unknown": True},
    {"min_vehicles": 0},
])
def test_invalid_component_requirements_fail_discovery(tmp_path, monkeypatch, requires):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    value = manifest()
    value["components"][0]["type"] = "task"
    value["components"][0]["requires"] = requires
    path = write_manifest(tmp_path, value, "invalid-requirements.json")
    assert [issue.code for issue in plugins.PluginRegistry.discover((path,)).issues] == [
        "invalid_capability_requirement"]


def test_literal_ref_key_inside_config_default_is_not_a_schema_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    value = manifest()
    value["components"][0]["config_schema"] = {
        "type": "object", "properties": {
            "payload": {"type": "object", "default": {"$ref": "literal-value"}}}}
    registry = plugins.PluginRegistry.discover((write_manifest(tmp_path, value, "literal.json"),))
    assert registry.issues == ()
    assert registry.effective_config("example.pack/backend") == {
        "payload": {"$ref": "literal-value"}}


@pytest.mark.parametrize("schema", [
    {"type": "object",
     "definitions": {"item": {"$ref": "https://example.com/remote.json"}}},
    {"type": "object",
     "definitions": {"item": {"$ref": "https://example.com/remote.json"}},
     "properties": {"item": {"$ref": "#/definitions/item"}}},
    {"type": "object",
     "customSchemas": {"item": {"$ref": "https://example.com/remote.json"}},
     "properties": {"item": {"$ref": "#/customSchemas/item"}}},
])
def test_discovery_rejects_external_ref_nested_in_schema_targets(tmp_path, monkeypatch, schema):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    value = manifest()
    value["components"][0]["config_schema"] = schema
    registry = plugins.PluginRegistry.discover((write_manifest(tmp_path, value, "ref.json"),))
    assert [issue.code for issue in registry.issues] == ["invalid_schema"]


def test_config_errors_are_structured(tmp_path, monkeypatch):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    path = write_manifest(tmp_path, manifest(), "pack.json")
    registry = plugins.PluginRegistry.discover((path,))
    report = registry.preflight({"example.pack/backend": {"speed": -1}})
    assert not report.ok
    assert report.issues[0].code == "invalid_config"
    assert report.issues[0].details["path"] == ["speed"]
    with pytest.raises(plugins.PluginRegistryError):
        registry.resolve("example.pack/backend", expected_type="agent")


def test_schema_defaults_are_recordable_and_passed_to_factory_without_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    value = manifest()
    value["components"][0]["config_schema"] = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "speed": {"type": "number", "default": 3},
            "camera": {"type": "object", "default": {}, "properties": {
                "name": {"type": "string", "default": "front"}},
                "additionalProperties": False},
            "routes": {"type": "array", "items": {"type": "object", "properties": {
                "enabled": {"type": "boolean", "default": True}}}},
        },
    }
    path = write_manifest(tmp_path, value, "defaults.json")
    supplied = {"routes": [{}]}
    registry = plugins.PluginRegistry.discover((path,))
    expected = {"routes": [{"enabled": True}], "speed": 3, "camera": {"name": "front"}}
    assert registry.resolve("example.pack/backend", config=supplied).id == "example.pack/backend"
    assert registry.effective_config("example.pack/backend", supplied) == expected
    report = registry.preflight({"example.pack/backend": supplied})
    assert report.ok and report.effective_config["example.pack/backend"] == expected
    assert report.to_dict()["recordable_config"]["example.pack/backend"] == expected
    monkeypatch.setattr(plugins.importlib, "import_module",
                        lambda name: SimpleNamespace(create_backend=lambda *, config: config))
    assert registry.instantiate("example.pack/backend", supplied) == expected
    assert supplied == {"routes": [{}]}


def test_root_default_and_secret_fields_are_redacted_from_recording(tmp_path, monkeypatch):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    value = manifest()
    value["components"][0]["config_schema"] = {
        "type": "object", "default": {"public": "ready"},
        "properties": {
            "public": {"type": "string"},
            "token": {"type": "string", "writeOnly": True},
            "nested": {"type": "object", "properties": {
                "key": {"type": "string", "x-secret": True},
                "visible": {"type": "boolean"}}},
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "password": {"type": "string", "writeOnly": True}}}},
        },
    }
    registry = plugins.PluginRegistry.discover((write_manifest(tmp_path, value, "secrets.json"),))
    assert registry.effective_config("example.pack/backend") == {"public": "ready"}
    config = {"token": "s3cr3t", "nested": {"key": "hidden", "visible": True},
              "items": [{"password": "p4ss"}]}
    report = registry.preflight({"example.pack/backend": config})
    expected = {"token": "[REDACTED]", "nested": {
        "key": "[REDACTED]", "visible": True},
        "items": [{"password": "[REDACTED]"}]}
    assert report.ok
    assert registry.recordable_config("example.pack/backend", config) == expected
    assert report.recordable_config["example.pack/backend"] == expected
    assert "s3cr3t" not in json.dumps(report.to_dict())
    assert "hidden" not in json.dumps(report.to_dict())
    assert "p4ss" not in json.dumps(report.to_dict())
    assert config["token"] == "s3cr3t"


def test_local_ref_secret_is_redacted_before_serializing_preflight(tmp_path, monkeypatch):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    value = manifest()
    value["components"][0]["config_schema"] = {
        "type": "object",
        "$defs": {"secret": {"type": "string", "writeOnly": True}},
        "properties": {"token": {"$ref": "#/$defs/secret"}},
        "additionalProperties": False,
    }
    registry = plugins.PluginRegistry.discover((write_manifest(tmp_path, value, "ref.json"),))
    supplied = {"token": "sample-only-secret"}
    assert registry.recordable_config("example.pack/backend", supplied) == {
        "token": "[REDACTED]"}
    assert "sample-only-secret" not in json.dumps(
        registry.preflight({"example.pack/backend": supplied}).to_dict())


def test_branch_pattern_and_contains_secrets_are_redacted(tmp_path, monkeypatch):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    value = manifest()
    value["components"][0]["config_schema"] = {
        "type": "object",
        "properties": {
            "mode": {"type": "string"},
            "token": {"type": "string"},
            "items": {"type": "array", "contains": {"type": "object", "properties": {
                "password": {"type": "string", "writeOnly": True}}}},
        },
        "anyOf": [{"properties": {"token": {"type": "string", "x-secret": True}}},
                  {"required": ["mode"]}],
        "if": {"properties": {"mode": {"const": "enabled"}}},
        "then": {"properties": {"token": {"type": "string", "writeOnly": True}}},
        "patternProperties": {"^private_": {"type": "string", "x-secret": True}},
        "additionalProperties": True,
    }
    registry = plugins.PluginRegistry.discover((write_manifest(tmp_path, value, "branches.json"),))
    supplied = {"mode": "enabled", "token": "top-secret", "private_key": "hidden",
                "items": [{"password": "p4ss"}]}
    report = registry.preflight({"example.pack/backend": supplied})
    assert report.ok
    recorded = json.dumps(report.to_dict())
    assert all(secret not in recorded for secret in ("top-secret", "hidden", "p4ss"))
    assert report.recordable_config["example.pack/backend"] == {
        "mode": "enabled", "token": "[REDACTED]", "private_key": "[REDACTED]",
        "items": [{"password": "[REDACTED]"}]}


@pytest.mark.parametrize("path,allowed", [
    ("artifacts/result.json", True),
    ("data", True),
    ("", False),
    ("/absolute/path", False),
    ("../escape", False),
    ("a/../escape", False),
    ("a\\b", False),
    ("C:\\drive\\file", False),
    ("a//b", False),
])
def test_episode_relative_path_schema_format(tmp_path, monkeypatch, path, allowed):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    value = manifest()
    value["components"][0]["config_schema"] = {
        "type": "object", "required": ["artifact"],
        "properties": {"artifact": {"type": "string", "format": "episode-relative-path"}},
        "additionalProperties": False,
    }
    registry = plugins.PluginRegistry.discover((write_manifest(tmp_path, value, "path.json"),))
    report = registry.preflight({"example.pack/backend": {"artifact": path}})
    assert report.ok is allowed
    if not allowed:
        assert report.issues[0].code == "invalid_config"
        with pytest.raises(plugins.PluginRegistryError):
            registry.resolve("example.pack/backend", config={"artifact": path})


@pytest.mark.parametrize("capability", [
    {"max_vehicles": "two"},
    {"max_vehicles": True},
    {"action_kinds": ["move"]},
    {"action_kinds": [{}]},
    {"sensor_resources": {"front": "rgb"}},
    {"bounded_execution": "yes"},
    {"component_api": "drone.plugin.api/v0.3"},
    {"component_api": 2},
])
def test_malformed_manifest_capability_rejected_before_import(tmp_path, monkeypatch, capability):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    value = manifest()
    value["components"][0]["capabilities"] = capability
    registry = plugins.PluginRegistry.discover((write_manifest(tmp_path, value, "bad-cap.json"),))
    assert "invalid_capability" in {issue.code for issue in registry.issues}


@pytest.mark.parametrize("api", ["drone.plugin.api/v0.1", "drone.plugin.api/v0.2"])
def test_component_api_versions_are_discoverable(tmp_path, monkeypatch, api):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    value = manifest()
    value["components"][0]["capabilities"] = {"component_api": api}
    registry = plugins.PluginRegistry.discover((write_manifest(tmp_path, value, "api.json"),))
    assert not registry.issues
    assert registry.resolve("example.pack/backend").capabilities["component_api"] == api


def test_static_capability_requirements_are_checked_before_factory_import(tmp_path, monkeypatch):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    value = manifest()
    value["components"][0]["capabilities"] = {
        "action_kinds": ["example/move"], "sensor_types": ["example/rgb"],
        "sensor_resources": {"front": "example/rgb"}, "coordinate_frame": "NED",
        "time_bases": ["monotonic"], "max_vehicles": 2,
        "scenario_operations": ["load", "reset"], "bounded_execution": True,
        "truth_access": False, "interruptible": False,
    }
    registry = plugins.PluginRegistry.discover((write_manifest(tmp_path, value, "cap.json"),))
    valid = {"action_kinds": ["example/move"], "sensor_resources": {
        "front": "example/rgb"}, "min_vehicles": 2,
        "scenario_operations": ["load"], "bounded_execution": True}
    assert registry.preflight({"example.pack/backend": {}}, requirements={
        "example.pack/backend": valid}).ok
    registry.resolve("example.pack/backend", requirements=valid)
    invalid = {"action_kinds": ["example/hover"], "sensor_resources": {
        "down": "example/rgb"}, "min_vehicles": 3, "truth_access": True,
        "hard_cancel": True}
    report = registry.preflight({"example.pack/backend": {}}, requirements={
        "example.pack/backend": invalid})
    assert not report.ok
    assert {issue.details["field"] for issue in report.issues} == {
        "action_kinds", "sensor_resources", "max_vehicles", "truth_access", "hard_cancel"}
    assert {issue.code for issue in report.issues} == {
        "insufficient_capability", "hard_cancel_unverified"}
    with pytest.raises(plugins.PluginRegistryError):
        registry.resolve("example.pack/backend", requirements=invalid)
    malformed = registry.preflight({"example.pack/backend": {}}, requirements=[])
    assert malformed.issues[0].code == "invalid_capability_requirement"


@pytest.mark.parametrize("claim", ["interruptible", "isolated"])
def test_hard_cancel_requires_runtime_proof_not_manifest_claim(tmp_path, monkeypatch, claim):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    value = manifest()
    value["components"][0]["capabilities"] = {claim: True}
    registry = plugins.PluginRegistry.discover((write_manifest(tmp_path, value, "cancel.json"),))
    selection = {"example.pack/backend": {}}
    assert registry.preflight(selection).ok
    report = registry.preflight(selection, requirements={
        "example.pack/backend": {"hard_cancel": True}})
    assert not report.ok
    assert [issue.code for issue in report.issues] == ["hard_cancel_unverified"]
    with pytest.raises(plugins.PluginRegistryError) as caught:
        registry.resolve("example.pack/backend", requirements={"hard_cancel": True})
    assert [issue.code for issue in caught.value.issues] == ["hard_cancel_unverified"]


def test_whole_config_secret_does_not_break_preflight_serialization(tmp_path, monkeypatch):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    value = manifest()
    value["components"][0]["config_schema"] = {
        "type": "object", "x-secret": True,
        "properties": {"token": {"type": "string"}}}
    registry = plugins.PluginRegistry.discover((write_manifest(tmp_path, value, "whole-secret.json"),))
    report = registry.preflight({"example.pack/backend": {"token": "classified"}})
    assert report.ok
    assert registry.recordable_config("example.pack/backend", {"token": "classified"}) == {}
    assert report.to_dict()["recordable_config"]["example.pack/backend"] == {}
    assert "classified" not in json.dumps(report.to_dict())


def test_invalid_secret_value_is_not_echoed_by_preflight_issue(tmp_path, monkeypatch):
    monkeypatch.setattr(plugins.metadata, "distributions", lambda: [])
    value = manifest()
    value["components"][0]["config_schema"] = {
        "type": "object", "properties": {
            "token": {"type": "integer", "writeOnly": True}}}
    registry = plugins.PluginRegistry.discover((write_manifest(tmp_path, value, "secret-invalid.json"),))
    report = registry.preflight({"example.pack/backend": {"token": "sample-secret"}})
    assert not report.ok
    assert report.issues[0].details["path"] == ["token"]
    assert report.issues[0].details["schema_path"] == ["properties", "token", "type"]
    assert "sample-secret" not in json.dumps(report.to_dict())
