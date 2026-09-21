# Drone spatial-intelligence platform

This repository is preparing a simulator-independent platform for drone tasks.
The present, runnable scope is a versioned platform contract, a small no-simulator MockBackend, shared episode fixtures, and the preserved legacy AirSim adapter. It is **not** yet a real AirSimBackend, EpisodeRunner, search task, benchmark suite, or a verified flight loop.

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

To inspect a built distribution, build a wheel with pip and run
`python scripts/verify_wheel.py <wheel-path>`. The check ensures the wheel
contains only the declared Python packages and distribution metadata; it must
not contain course materials, scenes, drivers, tests, or generated runs.

## Boundaries and real simulator work

`contracts/` is the platform-layer API. `simulator_contract/` remains the independent lower-level adapter boundary and is included in the wheel so a future backend does not need to import it from the working directory. The two Observation types are intentionally distinct: `PlatformObservation` is the per-step state handed to agents/tasks, while `simulator_contract.contracts.Observation` is metadata plus optional sensor binary payload.

The UE4/AirSim course material and `airsim-settings/drone.json` are frozen local resources. If present, verify their historic checksum with `python -B simulator_contract/freeze_baseline.py --verify`. This and no-simulator tests do not prove a real flight.

## Source and resource scope

The distributable package explicitly contains only the small Python packages listed in `pyproject.toml`. Large scenes, drivers, generated runs, and course resources are local references, not ordinary-source uploads. See [the upload checklist](docs/planning/GitHub上传前仓库整理清单.md) and [resource manifest](assets/manifests/legacy-ue4-airsim.md).

The private collaboration baseline is authorized for `seasky-right/drone-shot`; collaborators remain to be specified. Project licence and external-resource distribution rights remain unresolved. This repository does not publish external resources.
