# Plugin Contract v0.2 candidate

中文实操入口与 Core 变更判断见[插件开发交接](插件开发交接.md)。

Status: candidate, 2026-09-25. The public code is in
`contracts/plugin_v02.py`, `contracts/data_v02.py`, and
`contracts/migration_v02.py`. No affected-party confirmation has been
recorded. v0.1 episode records remain readable through `core.replay`.

## Install and inspect

Use Python 3.13. Both the helper and direct `pip wheel .` build Core from a
clean source stage, even when older `build/lib` output remains in the checkout:

```powershell
python scripts/build_core_wheel.py --output dist
python scripts/verify_wheel.py dist/drone_platform-0.1.0-py3-none-any.whl
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir dist
python scripts/build_builtin_pack.py --output dist
python -m pip install dist/drone_platform-0.1.0-py3-none-any.whl dist/drone_builtin_pack-0.1.0-py3-none-any.whl
drone-plugins list
```

The Core wheel contains only `core/` and `contracts/`. The separate builtin
Pack wheel contains the existing Mock/AirSim, ReachPoint, Agent, Evaluator and
lower-level adapter Python packages. Installing Core alone lists an empty
registry and imports no concrete components. AirSim execution still requires
`--enable-airsim` on the legacy single-episode CLI.

The installed-distribution entry-point group is `drone.plugins.v02`.
Each Pack wheel contains `<entry-point top-level package>/drone_plugin.json`
in RECORD. Discovery reads and validates this static file before any plugin
module import. One Pack can provide multiple stable component IDs. Required
manifest keys are `id`, `version`, `type`, `plugin_api`, `entry_point`,
`dependencies`, `capabilities`, and `config_schema`. A Pack also declares
`components`; each component has `id`, `type`, `entry_point`,
`capabilities`, and `config_schema`. See
`examples/external_plugin/src/drone_ext_sample/drone_plugin.json`.
Task, Agent, and Scenario components may also declare optional `requires`.
Single-component Task, Agent, and Scenario manifests may put `requires` at
the root. Omission means no component-specific Backend requirements.
Discovery validates this field without importing the component.
For a single-component manifest, Registry derives the component ID as
`<pack_id>/<type>`. A component may declare `capabilities.component_api` as
`drone.plugin.api/v0.1` or `drone.plugin.api/v0.2`; omission means v0.2.
The Pack-level `plugin_api` describes Registry/discovery compatibility and
does not upgrade a legacy component's runtime hooks.

`PluginRegistry.resolve` and `preflight` check identifiers, API/version
ranges, dependency cycles, component types, JSON Schema, config and ID
collisions. External references and unresolved local references fail during
discovery; only local JSON Pointer `$ref` values are supported. `instantiate`
imports the declared factory only after those checks. Factories accept
validated `config` and a restricted `CoreContext`.
Legacy single-vehicle adapters also receive `spec` where needed. A plugin
package is trusted Python code; this API is not a sandbox for untrusted uploads.
Core applies a root schema `default` when config is omitted, then missing
`properties` defaults recursively through objects, array `items` and
`prefixItems`, local `$ref`, and `allOf` in schema order. Defaults in
`anyOf`, `oneOf`, and conditional branches are not materialized; set those
values explicitly. `PluginRegistry.effective_config(id, config)` returns the
validated values passed to the factory without changing the caller's object.
`recordable_config(id, config)` redacts schema-marked secrets, and
`PreflightReport.to_dict()` serializes only recordable config. Config paths marked
`format: "episode-relative-path"` reject absolute paths, parent traversal,
Windows separators and empty path segments. Unmarked paths have no Core path
interpretation.

Standard manifest capability fields are checked for type and shape during
discovery. After installing the external sample Pack, check its declaration
before loading a factory:

```python
from core.plugins import PluginRegistry

registry = PluginRegistry.discover()
supported = registry.preflight(
    {"sample.mock/backend": {}},
    requirements={"sample.mock/backend": {
        "action_kinds": ["sample/move"],
        "min_vehicles": 2,
    }},
)
assert supported.ok

unavailable = registry.preflight(
    {"sample.mock/backend": {}},
    requirements={"sample.mock/backend": {
        "sensor_resources": {"front": "sample.sensor/rgb"},
    }},
)
assert not unavailable.ok
print([issue.to_dict() for issue in unavailable.issues])
```

Requirements may also name `sensor_types`, `coordinate_frame`, `environment`,
`time_bases`, `scenario_operations`, `scenario_id`, `truth_access`, and
`bounded_execution`.
`requires` uses these same keys, plus `sensor_resources`, `action_kinds`,
and `min_vehicles`. For example, a Task may declare
`{"sensor_types": ["sample.sensor/rgb"], "sensor_resources": {"front": "sample.sensor/rgb"}, "coordinate_frame": "local_ned"}`;
an Agent may declare `{"action_kinds": ["sample/move"]}`.
In `run-multi`, Core combines the selected Task, Scenario or Generator, and Agent
requirements with explicit run requirements. Arrays form a union; vehicle
minimums take the maximum; true flags remain required. Conflicting scalar
values or kinds for the same sensor resource return
`conflicting_capability_requirement`. Run config cannot remove a component
requirement. Each declared Backend capability is checked before any factory
import; the constructed Backend's `CapabilitySetV02` is checked before reset.
Scenario sources can require an `environment` supplied by the Backend, such as
`mock` or `airsim`. This checks the execution environment while allowing a
Backend to support multiple scenario IDs. An incompatible selection reports
`incompatible_environment` with both component IDs before Backend creation.
`environment` is a manifest-level declaration; `CapabilitySetV02` does not
expose a runtime environment field, so the runtime recheck retains that
declaration while verifying the other Backend capabilities.
The combined requirements are recorded in the episode entry.
`hard_cancel: true` is rejected with `hard_cancel_unverified` until a runtime
proof interface exists; an `interruptible` declaration alone is insufficient.
Failures have a code, manifest source, and details such as the
missing action or sensor ID. Config validation errors report the failing
JSON path and schema keyword without echoing submitted values. This first
check proves declarations only;
Backend capabilities and named sensor resources are checked again after
construction unless the Backend explicitly defers resource confirmation as
described below. It does not prove a physical sensor exists or that hard
cancellation works at runtime.

A Backend whose resource IDs can only be confirmed by its first observation
may declare `"sensor_resource_confirmation": "post_reset"` in its manifest
capabilities and list its possible `sensor_types`. For this opt-in, preflight
checks the required sensor kinds against those types; it does not claim that
any requested resource ID exists. The constructed Backend is checked the same
way before reset. Immediately after reset, before any Agent action, Core
requires every requested ID and kind in the Backend's confirmed
`CapabilitySetV02.sensor_resources`. A missing or mismatched resource records
`sensor_resources.confirm` in `events.jsonl`, closes the Backend and other
components, and leaves the episode `INCOMPLETE` without `result.json`.
`metadata/capabilities.json` records the pre-reset capability; a successful
confirmation additionally writes `metadata/capabilities-confirmed.json`.
Backends without this declaration retain strict ID and kind checks before
factory import and before reset. The Backend is responsible for deriving its
confirmed map from the actual reset observation, not merely from config.

## Run a v0.2 combination

`run-multi` can select either a fixed `components.scenario` or a
`components.generator` with a nonnegative top-level `seed`. A generator's
`generate(seed)` returns `ScenarioSpecV02` or an object with `.spec` and
optional `.truth`. The generated spec must record the requested seed. Only
Task and Evaluator receive truth; Agent observations and saved results do not.
When a Scenario or Generator declares `capabilities.scenario_id`, its returned
spec must use that ID. A mismatch leaves the episode incomplete.
An optional `components.evaluator` scores the completed episode before
`result.json` is finalized. It receives the result, trajectory, truth and a
local `artifact_root` for reading recorded sensor files, and
returns finite numeric `metrics`. An evaluator failure leaves `INCOMPLETE`.
The old fixed-Scenario path remains valid.

`run-benchmark` selects a top-level `benchmark` and optional `processor` in
addition to the normal episode components. Each `benchmark.cases()` entry has
a unique `case_id`, a `seed` (or `scenario_seed`) when using a generator, and
may override `task` or `components.scenario/generator/task/evaluator` for that
case. Core preflights all cases, runs each with the same selected Agent(s),
then saves per-case episode IDs, seeds, status and metrics under
`<output>/benchmarks/<benchmark_id>/result.json`. The optional Result
Processor receives read-only episode results for domain-specific aggregation.
Core records success counts but does not define a task-specific score formula.
Each generated case must have a nonnegative integer seed before the first case
runs. A case overriding the Generator with a fixed Scenario discards the base
seed; specifying a case seed with a fixed Scenario is rejected. A failed hook
leaves the benchmark record `INCOMPLETE` and does not publish a summary. Its
`events.jsonl` records the failing stage, case ID when known, and error text;
the failed episode has its own error event.
The Mock example exercises this flow without a simulator:

```powershell
drone-plugins run-benchmark builtin_pack/sample-benchmark-v02.json --output runs
```

This establishes orchestration, not valid randomization or benchmark policy
for a real world. A new World/Scenario Pack must implement feasible seeded
instances, stable resource identity and hidden truth; its Backend must report
actual load/reset support. Real scene and scoring acceptance remain separate.

`examples/external_plugin/sample.json` selects a Backend, Scenario, Task,
central Agent, two vehicle IDs, bindings, action IDs/schema and step budget.
The same `sample.urban` Scenario composes with `sample.search/task` and
`sample.mapping/task` in `mapping.json`. After installing the external
sample wheel, run:

```powershell
drone-plugins preflight examples/external_plugin/sample.json
drone-plugins validate-plugin --sample examples/external_plugin/sample.json --output runs
drone-plugins run-multi examples/external_plugin/mapping.json --output runs
drone-plugins show-result sample-two-vehicle --output runs
```

A Backend manifest with `requires_explicit_enable: true` requires
`--enable-backend` during v0.2 preflight and execution. It is rejected
before factory import without the flag.
The v0.2 config may request `required_sensor_resources` as a mapping of
resource IDs to sensor kinds. The CLI applies the manifest's resource
confirmation policy described above. A `require_hard_cancel: true` request
currently fails preflight.

The sample wheel source should be copied outside the repository before
building to prove it depends only on installed public packages. A source-tree
debug run may pass `--manifest <path>`; normal installed discovery needs no
path or project source. `validate-plugin` with no sample checks static
metadata only and reports `runtime_checked=false`. The Python entry point is
`core.conformance.validate_plugin(registry, sample=..., output=...,
component_cases=...)`. The report lists all ten categories under
`declared_types`, including absent categories. Use executable cases such as
`examples/external_plugin/component-cases.json`:

```powershell
drone-plugins validate-plugin --component-cases examples/external_plugin/component-cases.json --sample examples/external_plugin/sample.json --output runs
```

Each case maps a component ID to `{"config": {...}, "probe": {...}}`; `probe`
is optional. The kit constructs the component from its validated config,
executes its type-specific hook, checks the return value, then calls `close()`
even if the hook fails. JSON probe overrides accept Backend `scenario` and
`action`, Task `before_snapshot`, `after_snapshot` and `truth`, Agent
`snapshot` and `mode`, Evaluator `result`, Scenario Generator `seed`, Runtime
Provider `request`, and Result Processor `result`. Use `--enable-backend` only
for explicitly enabled Backends in a controlled environment.

| Type | Minimum executable check | Rejection example |
| --- | --- | --- |
| Backend | `capabilities/reset/execute/observe`; Manifest actions, sensor IDs, frame and capacity match runtime | Declared sensor resource missing at runtime |
| Task | `complete(snapshot, truth)` returns bool before and after an action | Non-boolean completion |
| Agent | `act(snapshot)` returns valid actions for known vehicles without truth in observations | Empty/foreign action or undeclared kind |
| Evaluator | `evaluate(result)` returns finite numeric metrics without changing input | Non-finite metric |
| Scenario | `.spec` is a round-trippable `ScenarioSpecV02`; truth obeys access level | Truth exposed when access is `none` |
| Scenario Generator | `generate(seed)` yields a repeatable, matching-seed Scenario | Wrong type, seed or declared scenario ID |
| Runtime Provider | `acquire(episode_id, request)` returns a resource and `release(resource)` executes | Missing release hook or release error |
| Training Driver | `run(session_factory)` resets and steps at least two distinct sessions | No repeated sessions |
| Result Processor | `process(read_result, ids)` returns JSON-safe summary without changing stored result | Invalid summary or write to supplied result |
| Benchmark | `cases()` returns JSON-safe cases with unique IDs | Duplicate case IDs |

`component_results` reports `passed`, `failed`, `unchecked`, or `unsupported`
with checks, failure stage and reason. Missing cases stay `unchecked`;
explicitly protected Backends without `--enable-backend` are `unsupported`
without factory import. Requested failed or unsupported cases make the CLI exit
nonzero. `valid` describes selected cases and any supplied sample;
`complete=true` requires every discovered component to pass, so unrelated
installed Packs without cases keep `status=partial`. Construction and
`close()` alone never count as a passing case. These are candidate minimal
hooks, not a guarantee of a component's domain correctness or physical
resource release.

The builtin Pack ships `component-cases.json` and `sample-v02.json`. Its ten
`drone.v02.mock/*` components have executable cases covering all ten types.
The fourteen existing Mock/AirSim/ReachPoint components explicitly declare
`component_api: drone.plugin.api/v0.1` and remain available through the
legacy adapter. They report `unsupported` for v0.2 conformance even without a
case, and their factories are not imported by that probe. With only the builtin
Pack installed, the report is 10 `passed`, 14 `unsupported`, zero `unchecked`,
`valid=true`, `complete=false`. This is full case coverage of the components
that claim v0.2, not v0.2 conformance of the legacy components.

A sample run executes and rereads a complete result. It also runs isolated
negative probes: changing
the registered action schema ID must reject the sample Agent action, leave
an `INCOMPLETE` record, and the Artifact Store must reject parent traversal.
Probe records are removed from a temporary directory. A repeated validation
allocates a new episode ID if the sample ID already exists; `run-multi` still
rejects explicit ID collisions. Backends declaring `requires_explicit_enable`
run only the supplied sample, with the repeated action probe listed under
`skipped_checks`. Preflight rejects invalid config before a factory import.
Unselected v0.2 components retain an explicit `unchecked` verdict; declared
v0.1 components retain `unsupported`.

Actions have `namespace/name` kinds and separate control, sampling, report
channels. An executor must register kind, payload schema ID and JSON Schema;
sampling/report channels need their own handler supplied by a Backend or Task
`action_handlers()` mapping keyed by `ActionChannel`. Observations contain public
state, explicit missing sensor reasons, artifact references and namespaced
extensions. Truth is passed to the Task only, never in the Agent snapshot.
The Backend reports actual sensor resource IDs after construction; capability
matching checks manifest declarations before import and actual resources
before reset. A synchronous Backend is responsible for bounded calls; a
deadline exceeded after return is recorded as timeout, not hard interruption.

The `EpisodeStore` writes `result.json` atomically, keeps `INCOMPLETE`
after failures, rejects artifact path traversal, and rereads complete results.
Plugin config snapshots replace JSON Schema fields marked `writeOnly` or
`x-secret` with `[REDACTED]`, including local `$ref`, branch, pattern, and
array schemas. Branch redaction is conservative and may hide additional
fields. The full effective config stays in memory for factory calls; do not
serialize it to run records. Legacy
single-vehicle records omit the Backend connection object because it has no
public secret schema. A scenario checksum/seed is recorded as declared; this alone
does not establish actual randomization. v0.2 multi-vehicle results are read
with `drone-plugins show-result`; v0.1 records use `drone-replay`.

## Compatibility and limits

| Input | Current rule |
| --- | --- |
| v0.1 Action/Observation | Explicit migration helpers map known fields; unknown kinds fail. |
| v0.1 TaskSpec/BackendConfig/result | Read with v0.1 parsers; the builtin Pack maps legacy component aliases. |
| v0.2 Action/Observation/Scenario | Strict version and field checks; unknown fields fail. |
| Unknown Plugin API or dependency | Registry rejects before factory import. |
| Builtin legacy component (`component_api=v0.1`) | Registry discovers it through the v0.2 Pack, while the legacy single-vehicle adapter runs it; v0.2 conformance reports `unsupported`. |
| Builtin `drone.v02.mock/*` | Ten type-specific executable cases and a two-vehicle Mock sample run through the v0.2 path. |
| Real AirSim dual vehicle | No acceptance evidence; the dual-vehicle path uses Mock only. |
| External untrusted upload | Unsupported; process/container isolation remains separate work. |

The reusable `EpisodeSession` adapts v0.1 single-vehicle components.
`MultiVehicleEpisode` exposes `reset/step/stop` for v0.2. Both paths use
`EpisodeLifecycle` for ordered events, cancellation checkpoints, runtime
acquisition/release and cleanup bookkeeping; their vehicle/task policies
remain separate. A failed result write leaves `INCOMPLETE`, and cleanup
errors do not replace the primary termination reason. Event subscribers run
after the event append and cannot abort the episode. Hard cancellation
is rejected at preflight until executor interruption has runtime proof. See
`docs/validation/可插拔Core阶段验证-2026-09-25.md` for exact evidence and gaps.
