"""Explicit, lossless upgrades for the v0.1 fields with known v0.2 meaning.

Legacy files remain readable by v0.1 readers. No generic schema rewrite exists:
task, backend, scoring and scenario configuration need a selected plugin pack.
"""

from __future__ import annotations

from .data_v02 import (
    ActionChannel, ActionV02, PlatformObservationV02, SensorReferenceV02,
)
from .model import (
    Action, ActionKind, ContractValidationError, PlatformObservation,
)


_SENSOR_KINDS = {
    "rgb": "drone.sensor/rgb",
    "depth": "drone.sensor/depth",
}


def migrate_action_v01(value: Action | object) -> ActionV02:
    """Map only the two v0.1 control actions; reject unknown legacy shape."""

    old = value if isinstance(value, Action) else Action.from_dict(value)
    if old.kind is ActionKind.MOVE_TO:
        kind = "drone.control/move_to"
        payload = {"target_position_ned": old.target_position_ned.to_dict()}
        payload_schema = "drone.action.move_to/v0.2"
    elif old.kind is ActionKind.HOVER:
        kind = "drone.control/hover"
        payload = {}
        payload_schema = "drone.action.hover/v0.2"
    else:
        raise ContractValidationError("unsupported v0.1 action; manual migration required")
    return ActionV02(old.action_id, old.vehicle_id, kind, ActionChannel.CONTROL,
                     payload_schema, payload, old.deadline_s)


def migrate_observation_v01(value: PlatformObservation | object) -> PlatformObservationV02:
    """Retain v0.1 public state and artifact references, without inventing truth."""

    old = value if isinstance(value, PlatformObservation) else PlatformObservation.from_dict(value)
    sensors = []
    for sensor in old.sensors:
        kind = _SENSOR_KINDS.get(sensor.kind)
        if kind is None:
            raise ContractValidationError(f"sensor kind {sensor.kind!r} needs an explicit namespace mapping")
        sensors.append(SensorReferenceV02(sensor.sensor_id, kind, sensor.relative_path,
                                          sensor.captured_wall_time_ns))
    state = {"position_ned": old.position_ned.to_dict(),
             "velocity_ned_mps": list(old.velocity_ned_mps)}
    return PlatformObservationV02(old.sequence, old.vehicle_id, old.wall_time_ns,
                                  state, tuple(sensors), old.missing_sensors,
                                  simulator_time_ns=old.simulator_time_ns)
