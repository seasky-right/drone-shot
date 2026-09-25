# C0 Plugin Contract v0.2 candidate

Status: candidate, pending affected parties' confirmation. Date: 2026-09-25.
This record describes a public boundary and focused contract tests; it is not C0-C6 acceptance.

## Dependency boundary

`core/` imports `contracts/`; plugin packs import `contracts/`. Backend packs may
import the separate `simulator_contract/` adapter. `contracts/` imports neither
Core nor plugin implementations; Core must not import course materials or AirSim RPC.
The Plugin API (`drone.plugin.api/v0.2`), platform data schema
(`drone.platform.contract/v0.2`), component package version, scenario version,
and scoring policy version are independent version axes.

`contracts.plugin_v02.PluginType` defines backend, task, agent, evaluator,
scenario, scenario generator, runtime provider, training driver, result processor,
and benchmark categories. One installed pack may declare multiple stable component
IDs and factories. The factory receives validated JSON configuration and a Core
context, then returns a component. Only `close` is universal; Core schedules other
role-specific hooks. Pack discovery, import order, dependency resolution and manifest
validation belong to the Registry.

## Data rules

Actions have stable action/vehicle IDs, a `namespace/name` kind, channel
(`control`, `sampling`, `report`), a named payload schema, JSON object payload,
relative deadline and optional correlation ID. Core and the executing pack must
validate the registered action payload schema; this envelope alone cannot prove
that an arbitrary plugin action is supported. Platform observations contain public
state, artifact references, explicit missing sensor reasons and namespaced
extensions. Truth is not a platform observation field and must be delivered only
to authorized Task/Evaluator contexts. Multi-vehicle snapshots map stable IDs to
observations and identify absent vehicles with reasons. Agent bindings allow one
agent to cover one or more vehicles.

Scenario identity, version/checksum, resources, seed, initial states and truth
access are independent of Task and Backend. Capability declarations include
actions, sensor types and actual resource IDs, frame/time, vehicle limit, scenario
operations, truth access and bounded execution. A type declaration alone cannot
prove a named physical sensor is available; Core must recheck discovered resources.
Task, Agent, and Scenario Manifest entries may add optional `requires` using
the Backend capability requirement keys. Core unions list requirements and
preserves all true flags across selected components and run config. Conflicting
frame, scenario ID, or named resource kinds fail preflight with a structured
issue. Backend Manifest declarations are checked before factory import, and
the resulting `CapabilitySetV02` is checked before reset. Requirements
omitted by older manifests remain empty; hard cancellation remains rejected.

All v0.2 readers reject unknown fields, unsupported schema versions, non-finite
numbers, non-JSON payloads and unsafe artifact paths. Unknown plugin action kinds
must be rejected by the action registry before execution, even if their envelope
parses. Relative paths resolve under the episode artifact root. The v0.2 entry
records validated config with `writeOnly` and `x-secret` fields redacted;
unannotated secret fields remain a plugin author's responsibility.

## v0.1 compatibility matrix

| Source | Rule |
| --- | --- |
| v0.1 action `move_to` / `hover` | `migrate_action_v01` maps to `drone.control/move_to` / `drone.control/hover` and explicit payload schemas. Other kinds fail. |
| v0.1 platform observation | `migrate_observation_v01` preserves public NED state, timing, missing reasons and RGB/depth artifact references. Unknown sensor kinds require a named mapping. No truth is inferred. |
| v0.1 TaskSpec, BackendConfig, score or episode result | Continue reading with v0.1 readers. Conversion to plugin IDs, Scenario or policy requires an explicitly selected pack and migration tool; no guessed default. |
| Unknown fields or future version | Reject with `ContractValidationError`; do not silently drop data or choose a nearby version. |

The wire fixtures live in `fixtures/plugin_v02/`, with negative cases and an AST
dependency check in `tests/test_plugin_contract_v02.py`. The check covers the
public `contracts/` package and v0.2 execution module. `core/cli.py`,
`core/experiment_cli.py`, and Console now resolve factories through the
Registry. `core/multi_vehicle.py` supports two-vehicle Mock execution,
central/per-vehicle binding, capability rejection, JSON payload validation,
per-action outcomes, reset/step/stop, cancellation, monotonic budgets and
cleanup errors. The CLI persists and rereads that path. An independent
external wheel has been installed and run against the unchanged Core wheel.
This is technical evidence, not affected parties' protocol confirmation or
real-simulator acceptance. The v0.1 `EpisodeSession` and v0.2
`MultiVehicleEpisode` use the shared `EpisodeLifecycle` for ordered events,
cancellation checkpoints, runtime acquire/release and cleanup bookkeeping.
They retain separate vehicle/task policies. Event subscribers cannot abort
an episode; hard interruption remains unsupported and a request for it is
rejected at Registry preflight with `hard_cancel_unverified`.
