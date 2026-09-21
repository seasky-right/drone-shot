# Simulator contract and legacy baseline

`材料-无人机遥感实习-AirSIM部分` is frozen as the verified UE4.25.4
Microsoft AirSim baseline. It contains a packaged scene, legacy AirLib, the
Python client, launcher, console, and validation scripts. No new task code may
import, modify, or depend on those files directly. The only compatibility entry
point is `legacy_airsim.py` in this directory.

## Boundary

The v1 contract (`drone.simulator.contract/v1`) exposes three small interfaces:

1. `SimulatorSession` owns connection lifecycle and capability discovery.
2. `VehicleControl` accepts bounded vehicle commands and exposes vehicle state.
3. `SensorReader` returns sensor observations.

`DroneSimulator` merely composes these protocols for simple consumers; it is
not an invitation to add every possible simulator operation to one interface.
New needs should first be modelled as a focused protocol with a versioned data
type, for example `MapReader` or `ScenarioControl`.

`declared_capabilities` and `capabilities(vehicle_id)` describe operation types
that an adapter can translate. `sensors(vehicle_id)` separately lists concrete
resources already discovered for that vehicle. A caller can issue an explicit
first sensor request only when the capability exists, then treat a successful
response as discovery of that `sensor_id`; it must not assume any configured or
nominal sensor exists merely because RGB/depth operation support is declared.

## Data conventions

| Field | Convention |
| --- | --- |
| Distance, velocity, duration | meters, meters/second, seconds |
| World frame | NED: north, east, down |
| Vehicle body frame | FRD: forward, right, down |
| Orientation | unit quaternion `(w, x, y, z)` relative to NED |
| Time | optional `simulator_time_ns` plus always-recorded `wall_time_ns` |
| Identity | opaque `vehicle_id` and `sensor_id`, carried on all state/observation records |

NED/FRD is the canonical contract because it matches common aerospace
navigation and the legacy AirSim baseline. Coordinate conversion belongs to
backend adapters only. The legacy AirSim adapter passes AirSim's NED data
through because it already matches the contract. A simulator using ENU, FLU,
geodetic, or engine-local axes must convert before returning contract data and
document its origin frame.

Every public payload carries `SCHEMA_VERSION`. `CommandResult` reports whether
the command was accepted and completed. Failures use `ContractError` with a
stable `ErrorCode`, an actionable message, retryability, and string details.
State and sensor reads return their typed value on success and raise
`SimulatorContractException` on failure; its `error` property carries the same
structured error data rather than exposing simulator-specific exception types.

## Observations and payloads

`ObservationMetadata` is small and serializable. `Observation.payload` is an
optional byte stream and is intentionally separate from metadata. Persist or
stream image and point-cloud payloads by reference or as binary files; do not
base64 them into JSON. A caller may request metadata only with
`SensorRequest(include_payload=False)`.

The baseline adapter declares RGB and depth translation support. It records a
specific `sensor_id` in `sensors(vehicle_id)` only after a successful
observation confirms that resource. Float depth is packed into little-endian float32 bytes
(`airsim-float32`); a future backend should retain an explicit encoding in its
metadata. Image `frame_id` names the camera optical frame. When AirSim returns
`camera_position` and `camera_orientation`, `pose_ned` supplies that optical
frame's pose relative to NED and metadata marks this with
`pose_reference_frame=ned`.

## Legacy adapter scope

`AirSimLegacyAdapter` handles only RPC exchange. It does not launch the UE4
process, click the `Choose Vehicle` modal, or reproduce the old landing
fallback. Those behaviors remain frozen in `safe_launcher.py`,
`control_console.py`, and `verify_airsim.py` as regression evidence. The
adapter's `vehicle_id` must still use the validated default-vehicle fallback
policy at orchestration level, because this packaged scene may not expose the
configured `Drone` vehicle.

## Baseline audit

Startup path: `安全启动无人机仿真.cmd` -> `safe_launcher.py` -> packaged
`ModularNeighborhood.exe` -> modal selection -> `control_console.py`.
Validation path: `verify_airsim.py` starts the packaged scene hidden, connects
through AirSim Python RPC, obtains state and camera `0`, and optionally takes
off, moves, and lands.

AirSim coupling is present in the launch paths, raw MessagePack RPC methods,
the Python `airsim` client, `drone.json`, and all assets under `AirLib-1.4`.
The latter also contains vendored third-party code and is not an appropriate
search target for application dependency audits.

The existing automated coverage is `PythonClient/tests/test_py313_smoke.py`
(three import/type compatibility checks). `verify_airsim.py` is an integration
check that starts a GUI process and must not be run in routine contract tests.

## Frozen-baseline checksum scope

`legacy_ue4_airsim.manifest.json` records SHA-256 values for launch scripts,
Python clients, `AirLib-1.4` source/binaries, packaged scene files, and the
root-level `airsim-settings/drone.json`. Recreate it with
`python simulator_contract/freeze_baseline.py --write`, or verify it with
`python simulator_contract/freeze_baseline.py --verify`. The script excludes
runtime-mutated `Saved/Logs`, `Saved/Crashes`, `__pycache__`, and generated
`PythonClient/build`, `build`, and `dist` artifacts. A checksum tells us
whether the reference artifact changed; it does not certify that the packaged
simulator still runs.
