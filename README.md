# Drone spatial-intelligence platform

This repository is preparing a simulator-independent platform for drone tasks.
The runnable P2 scope is a versioned platform contract, EpisodeRunner and Recorder, a no-simulator MockBackend, ReachPoint task/agent/evaluator, and an AirSimBackend built on the preserved low-level adapter. The AirSimBackend has FakeRpc tests; a real simulator flight and the P3 integrated flight loop have not yet been verified. Track A also has Mock-only batch execution and read-only replay; a real-simulator benchmark remains later work.

Read [the current route](综述.md) first, then the [planning index](docs/planning/README.md). The former old overview is archived under `docs/archive/` and is not a delivery baseline.

## Install and test without a simulator

Python 3.13 is required; P0 was validated with CPython 3.13.5. A normal install has no runtime third-party dependencies and does not require Unreal, AirSim, course resources, or `.driver-cache`.

```text
python -m venv .venv
.venv\\Scripts\\python -m pip install --upgrade pip
.venv\\Scripts\\python -m pip install .
.venv\\Scripts\\python -B -m unittest discover -s tests -v
.venv\\Scripts\\python -B -m unittest discover -s simulator_contract/tests -v
```

`requirements-dev.lock` records an empty third-party test set. `requirements-build.lock` mirrors the exact PEP 517 build requirement in `pyproject.toml`; verify the mirror with `python -B scripts/verify_dependency_lock.py`. Install `.[legacy-airsim]` only when deliberately using the preserved legacy adapter.

`config.example.json` is a checked-in `BackendConfig` plus `TaskSpec` example for the Mock backend. Use a `config.local.json` or `configs/<name>.local.json` file for machine-specific settings; those paths are ignored.

Run one no-simulator ReachPoint episode with `drone-run --config config.example.json --agent fixed --output runs` after installation, or `python -B run.py --config config.example.json --agent fixed --output runs` from the repository. Results include a task/backend snapshot, trajectory, events, and `result.json`. `--agent hover` exercises an alternative Agent. The CLI also accepts `--agent direct` with `configs/reach_point.example.json` and `--agent fixed-route` with `configs/reach_point_fixed_route.example.json`, using the merged Agent branch implementations. The pushed `LawnmowerSearchAgent` is a navigation component; a SearchTarget Task/Evaluator and real search benchmark remain P4 work. The CLI supports Mock episodes in P2; real AirSim smoke checks use `python -B -m backends.airsim.smoke` with an explicitly prepared simulator and configuration.

## Track A Mock experiments

`drone-experiment --config experiment.example.json --output experiments` runs the same Mock task with three Agent configurations across 20 seed values. The generic `ExperimentManager` records every attempted episode, aggregates success and numeric metrics, and writes `config.json`, `summary.json`, `metrics.csv` and `report.md`. The CLI uses Mock Agent/Task/Evaluator stand-ins; the platform manager itself depends only on the shared interfaces. `drone-replay <episode-directory>` rereads and validates saved trajectory, events, result and sensor references without sending flight commands.

MockBackend has no scene randomization. The seed is passed through `TaskSpec`; repeated seed values are comparable inputs, not evidence of independent random scenes. Real benchmarking still requires P3 validation and F11 comparison rules.

To inspect a built distribution, build a wheel with pip and run
`python scripts/verify_wheel.py <wheel-path>`. The check ensures the wheel
contains only the declared Python packages and distribution metadata; it must
not contain course materials, scenes, drivers, tests, or generated runs.

## Boundaries and real simulator work

`contracts/` is the platform-layer API. `simulator_contract/` remains the independent lower-level adapter boundary; `backends/airsim/` maps between them without exposing AirSim RPC to the platform core. The two Observation types are intentionally distinct: `PlatformObservation` is the per-step state handed to agents/tasks, while `simulator_contract.contracts.Observation` is metadata plus optional sensor binary payload.

The UE4/AirSim course material and `airsim-settings/drone.json` are frozen local resources. If present, check their historic checksum with `python -B simulator_contract/freeze_baseline.py --verify`. The current manifest check reports six missing `.pytest_cache` files in the frozen course directory; all remaining 1,383 entries match. This and no-simulator tests do not prove a real flight.

## Source and resource scope

The distributable package explicitly contains only the small Python packages listed in `pyproject.toml`. Large scenes, drivers, generated runs, and course resources are local references, not ordinary-source uploads. See [the upload checklist](docs/planning/GitHub上传前仓库整理清单.md) and [resource manifest](assets/manifests/legacy-ue4-airsim.md).

The private collaboration baseline is authorized for `seasky-right/drone-shot`; collaborators remain to be specified. Project licence and external-resource distribution rights remain unresolved. This repository does not publish external resources.

## Offline ReachPoint evaluation

The local evaluator scores versioned episode JSON records and compares runs made under the same conditions without requiring a simulator. It writes `result.json`, `summary.json`, and a Markdown comparison report. See the [local evaluation guide](docs/evaluation/本地评价使用说明.md). The included trajectories are synthetic fixtures rather than AirSim flight results.

Validate the versioned ReachPoint pack with `python -B scripts/validate_reach_point_pack.py`. The pack defines one case, three baselines, and five repeats. An adapter from this pack manifest to the current `ExperimentManager`, plus trusted elapsed-time and event recording, is still required before the planned 15 benchmark episodes can be treated as an official result.
