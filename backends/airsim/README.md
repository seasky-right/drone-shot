# P2 AirSimBackend

The simulator must already be running. This backend does not start or stop UE4.

`BackendConfig.backend_type` is `airsim`; its nonempty `vehicle_id` is a
platform identifier. `connection.rpc_vehicle_id` defaults to the empty AirSim
vehicle name used by the legacy scene. Set it explicitly for a named AirSim
vehicle. The backend uses the low-level `AirSimLegacyAdapter`, not course
materials or direct AirSim imports.

P2 platform actions are only `move_to` and `hover`. `reset` connects,
resets the simulator, takes API control, arms, verifies the configured home
position, takes off and checks altitude. `cleanup` returns to home N/E at a
flight height, then lands and releases control. `close` releases the RPC
connection and does not implicitly fly the vehicle. The simulator-wide reset
can affect other vehicles; this backend is single-vehicle only.

All positions are world NED metres: north +X, east +Y, down +Z. The
`TaskSpec.home_position_ned` is a world coordinate, distinct from the world
origin. `move_to` computes world NED error, converts horizontal command to
body forward/right using state quaternion yaw, and sends bounded velocity
segments. An RPC reply alone is insufficient; each segment is followed by a
new state read and distance check. A monotonic deadline and configurable
`position_tolerance_m` bound the action. `hover` confirms that the full
three-axis NED velocity is within `hover_speed_tolerance_mps`. Task success
is still decided by the Task from its post-action observation.

Supported `connection` settings include `host`, `port`,
`rpc_vehicle_id`, `connect_timeout_s`, `home_tolerance_m`,
`takeoff_timeout_s`, `takeoff_confirm_timeout_s`,
`takeoff_min_height_m`, `move_speed_mps`, `control_interval_s`,
`position_tolerance_m`, `hover_speed_tolerance_mps`,
`state_poll_interval_s`, `return_height_m`, `return_timeout_s`,
`land_timeout_s`, `land_confirm_timeout_s`, and
`near_ground_tolerance_m`. Values are validated when used. Set
`takeoff_on_reset` to true; disabling it is unsupported in this P2 slice.
`sensors` is a list of `{"sensor_id": "0", "kind": "rgb"}` or `depth`.
A binary sample is written only after the adapter returns a nonempty payload.
A depth failure or empty payload appears in `missing_sensors`; no image is
fabricated. Every capture gets a new file opened exclusively, so repeated
observations at the same action sequence cannot overwrite earlier images.
Each `SensorReference.relative_path` is relative to
`BackendConfig.resource_root`. The EpisodeRunner sets this root to its
episode directory for every backend and records the effective config;
independent smoke runs set their own output root. A FakeRpc episode verifies
capture, recording, and replay; real simulator behavior remains unverified.
Platform observations contain NED position/velocity and sensor file
references, while low-level sensor metadata remains in the adapter layer.

The old scene may continue to report Flying after landing. The fallback
requests near-ground `moveToZ`, retries land and checks proximity to the
configured home altitude before disarming. `runtime_metadata.landing_confirmation`
reports `landed_state`, `landed_state_after_retry`, or
`near_ground_disarmed_compatibility`. The last value is a compatibility
criterion, **not proof of physical touchdown or a real-simulator safety
validation**. If return or landing fails, `CleanupResult` reports failure
separately from the episode's original reason.

## Independent smoke run

Prepare a JSON file containing `backend_config` and `task_spec` in the
platform v0.1 formats, plus `waypoint_ned` as a PositionNed object. Choose
a safe waypoint, speed, tolerances and timeouts for the actual scene before
running. The command parses without flight unless `--execute` is supplied:

```powershell
python -m backends.airsim.smoke .\smoke-config.json .\smoke-evidence
python -m backends.airsim.smoke .\smoke-config.json .\smoke-evidence --execute
```

The output directory must not already exist. The run writes
`smoke_result.json` and requested binary sensor files, including observations,
actions, cleanup and runtime metadata. This is independent Backend evidence,
not a Runner episode. No real simulator run was performed during P2 code
development; FakeRpc tests establish only mapping and control-flow behavior.
