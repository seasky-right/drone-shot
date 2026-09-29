# Drone Design Mock Pack

This independently installable v0.2 Backend Pack exposes two drone design
settings through the public plugin configuration: `max_speed_mps` and
`battery_capacity_wh`. `step_duration_s` is the discrete Mock model step,
not a vehicle property or measured flight time. The manifest validates all
three positive values before factory import. The schema supplies the sample
defaults (2 m/s, 1 s, 6 Wh) when config is omitted; a run can explicitly
override them. The Backend accepts the builtin
Mock scenario, task, and agent without changes to Core or their contracts.

Each `drone/move` action advances at most `max_speed_mps * step_duration_s`
along the local NED north axis. The action deadline is only the existing
execution deadline; it does not define the model step. Movement consumes a
fixed 1 Wh per meter;
available battery can shorten a step, and a later move fails when depleted.
One Backend config applies to every vehicle in the episode. Each vehicle has
independent position and remaining battery state; per-vehicle design settings
are outside this sample's scope. `reset` restores both.
The platform observation reports `north_m` and `battery_remaining_wh` as state,
not as a low-level sensor reading. No mass, aerodynamics, acceleration, charging,
or real AirSim vehicle settings are modeled.

Install the Core and builtin Pack first, then build and install this Pack in a
Python 3.13 environment with `packaging` and `jsonschema` available:

```powershell
python -m pip wheel examples/drone_design_pack --no-deps --wheel-dir dist
python -m pip install --no-deps dist/drone_design_sample-0.1.0-py3-none-any.whl
drone-plugins list
```

For source checkout debugging without installing this Pack, supply its manifest
to `drone-plugins` and put `examples/drone_design_pack/src` on `PYTHONPATH`.
The builtin Pack must still be installed or separately supplied via `--manifest`.

Run the example and reread its completed record:

```powershell
drone-plugins run-multi examples/drone_design_pack/sample.json --output runs
drone-plugins show-result sample-drone-design --output runs
```

Changing the backend config values in the sample changes the Mock movement
trace; the sample reaches north=5 in three discrete steps with 1 Wh remaining.
