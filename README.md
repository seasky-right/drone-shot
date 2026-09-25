# Drone spatial-intelligence platform

This repository is preparing a simulator-independent platform for drone tasks.
The current Plugin Contract v0.2 candidate has a Core/Contracts wheel, a separate first-party component Pack, static plugin discovery/preflight, and a two-vehicle Mock execution path. The earlier single-vehicle ReachPoint and AirSim routes remain available through explicit adapters. One real UE4 AirSim ReachPoint success and one controlled failure have been recorded and replayed; P3 formal acceptance and a real-simulator benchmark remain later work. See the [plugin developer guide](docs/plugins/README.md) for the new API and its limits.

Read [the current route](综述.md) first, then the [planning index](docs/planning/README.md) and [current status report](docs/validation/项目现状报告-2026-09-25.md). The former old overview is archived under `docs/archive/` and is not a delivery baseline.

## Install and test without a simulator

Python 3.13 is required; P0 was validated with CPython 3.13.5. Core now depends on `packaging` and `jsonschema` for version and configuration checks. Mock use does not require Unreal, AirSim, course resources, or `.driver-cache`.

```text
python -m venv .venv
.venv\\Scripts\\python -m pip install --upgrade pip
.venv\\Scripts\\python scripts/build_core_wheel.py --output dist
.venv\\Scripts\\python scripts/build_builtin_pack.py --output dist
.venv\\Scripts\\python -m pip install dist/drone_platform-0.1.0-py3-none-any.whl dist/drone_builtin_pack-0.1.0-py3-none-any.whl
.venv\\Scripts\\python -B -m unittest discover -s tests -v
.venv\\Scripts\\python -B -m unittest discover -s simulator_contract/tests -v
```

`requirements-dev.lock` records the absence of separate test-only dependencies. `requirements-build.lock` mirrors the PEP 517 build requirement in `pyproject.toml`; verify it with `python -B scripts/verify_dependency_lock.py`. The first-party Pack carries the preserved lower-level AirSim adapter and its `msgpack` dependency.

`config.example.json` is a checked-in `BackendConfig` plus `TaskSpec` example for the Mock backend. Use a `config.local.json` or `configs/<name>.local.json` file for machine-specific settings; those paths are ignored.

Run one no-simulator ReachPoint episode with `drone-run --config config.example.json --agent fixed --output runs` after installation, or `python -B run.py --config config.example.json --agent fixed --output runs` from the repository. Results include a task/backend snapshot, trajectory, events, and `result.json`. `--agent hover` exercises an alternative Agent. The CLI also accepts `--agent direct` with `configs/reach_point.example.json` and `--agent fixed-route` with `configs/reach_point_fixed_route.example.json`, using the merged Agent branch implementations. The pushed `LawnmowerSearchAgent` is a navigation component; a SearchTarget Task/Evaluator and real search benchmark remain P4 work. The same ReachPoint configuration can select AirSim by setting `backend_config.backend_type` to `airsim` and supplying the local simulator connection values. Set scene-specific `task_spec.parameters.action_deadline_s`, waypoint, speed and tolerance values before flight, then use `python -B run.py --config configs/airsim.local.json --agent fixed-route --output runs --enable-airsim` with a prepared simulator. The explicit switch authorizes this run to connect, take off, move, return home, and land. The existing Mock command needs no switch. Independent real AirSim smoke checks use `python -B -m backends.airsim.smoke`; full simulator validation remains pending.

## Compact terminal view

`drone-console` shows the implemented functions and recent episode results. `drone-console run --config config.example.json --output runs` shows connection, actions, positions, cleanup, metrics and the recording path while running one Mock ReachPoint episode. Use `drone-console status --output runs` to scan recent records and `drone-console show --output runs` to inspect the latest complete episode; `show <episode-directory>` selects a specific record. From a source checkout, replace `drone-console` with `python -B -m core.console`.

For a real scene, use a locally prepared AirSim configuration with `drone-console run --config <local-config> --agent fixed-route --enable-airsim`. The console connects to an **already running** simulator and does not start or open its window. It does not hide a window that is already visible. Keep the scene's safety parameters and startup procedure from the [P3 flight record](docs/validation/P3-AirSim-真实飞行验证报告.md); this command is not an automatic scene launcher.

## Track A Mock experiments

`drone-experiment --config experiment.example.json --output experiments` runs the same Mock task with three Agent configurations across 20 seed values. The generic `ExperimentManager` records every attempted episode, aggregates success and numeric metrics, and writes `config.json`, `summary.json`, `metrics.csv` and `report.md`. The CLI resolves Agent/Task/Evaluator factories from installed Pack metadata; the platform manager itself depends only on the shared interfaces. `drone-replay <episode-directory>` rereads and validates saved trajectory, events, result and sensor references without sending flight commands.

For a batch using the merged ReachPoint Task/Evaluator and three Agent implementations, run `drone-experiment --config reachpoint.experiment.example.json --output experiments/reachpoint-local`. This is a Mock route and recorder comparison; see [the ReachPoint batch report](docs/validation/Track-A-ReachPoint-Mock批量实验报告.md) for the 60-episode result and limits.

MockBackend has no scene randomization. The seed is passed through `TaskSpec`; repeated seed values are comparable inputs, not evidence of independent random scenes. Real benchmarking still requires P3 validation and F11 comparison rules.

Build Core from clean staging with `python scripts/build_core_wheel.py`, then run
`python scripts/verify_wheel.py <wheel-path>`. This checks that the Core wheel
contains only `core/`, `contracts/` and distribution metadata. The first-party
components are built by `scripts/build_builtin_pack.py`.

## Boundaries and real simulator work

`contracts/` is the platform-layer API. `simulator_contract/` remains the independent lower-level adapter boundary; `backends/airsim/` maps between them without exposing AirSim RPC to the platform core. The two Observation types are intentionally distinct: `PlatformObservation` is the per-step state handed to agents/tasks, while `simulator_contract.contracts.Observation` is metadata plus optional sensor binary payload.

The UE4/AirSim course material and `airsim-settings/drone.json` are frozen local resources. If present, check their historic checksum with `python -B simulator_contract/freeze_baseline.py --verify`. The current manifest check reports six missing `.pytest_cache` files in the frozen course directory; all remaining 1,383 entries match. This and no-simulator tests do not prove a real flight.

## Source and resource scope

The Core and first-party Pack are separate wheels. Large scenes, drivers, generated runs, and course resources are local references, not ordinary-source uploads. See [the upload checklist](docs/planning/GitHub上传前仓库整理清单.md) and [resource manifest](assets/manifests/legacy-ue4-airsim.md).

The private collaboration baseline is authorized for `seasky-right/drone-shot`; collaborators remain to be specified. Project licence and external-resource distribution rights remain unresolved. This repository does not publish external resources.

## Offline ReachPoint evaluation

The local evaluator scores versioned episode JSON records and compares runs made under the same conditions without requiring a simulator. It writes `result.json`, `summary.json`, and a Markdown comparison report. See the [local evaluation guide](docs/evaluation/本地评价使用说明.md). The included trajectories are synthetic fixtures rather than AirSim flight results.

Validate the versioned ReachPoint pack with `python -B scripts/validate_reach_point_pack.py`. The pack defines one case, three baselines, and five repeats. An adapter from this pack manifest to the current `ExperimentManager`, plus trusted elapsed-time and event recording, is still required before the planned 15 benchmark episodes can be treated as an official result.
