"""Small parameterized Mock backend using the public v0.2 plugin hooks."""

from __future__ import annotations

import math
import time
from typing import Mapping

from contracts.data_v02 import (
    ActionChannel, ActionV02, CapabilitySetV02, EpisodeSnapshotV02,
    PlatformObservationV02, ScenarioSpecV02,
)
from core.multi_vehicle import ActionOutcome


SCENARIO_ID = "drone.v02.mock"
ACTION_KIND = "drone/move"
ACTION_SCHEMA = "drone.move/v1"
ENERGY_WH_PER_METER = 1.0


def register(*, config=None):
    return None


class DesignedMockBackend:
    def __init__(self, config: Mapping[str, object]) -> None:
        self.max_speed_mps = self._positive(config["max_speed_mps"], "max_speed_mps")
        self.step_duration_s = self._positive(config["step_duration_s"], "step_duration_s")
        self.battery_capacity_wh = self._positive(config["battery_capacity_wh"],
                                                  "battery_capacity_wh")
        self.positions: dict[str, float] = {}
        self.remaining_wh: dict[str, float] = {}
        self.sequence = 0
        self.closed = False
        self.active = False

    @staticmethod
    def _positive(value: object, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a finite positive number")
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f"{name} must be a finite positive number")
        return number

    def capabilities(self) -> CapabilitySetV02:
        return CapabilitySetV02((ACTION_KIND,), (), {}, "local_ned", ("wall",),
                                2, ("load", "reset"), True, True)

    def reset(self, scenario: ScenarioSpecV02,
              vehicle_ids: tuple[str, ...]) -> EpisodeSnapshotV02:
        if self.closed:
            raise RuntimeError("backend is closed")
        if scenario.scenario_id != SCENARIO_ID or not 1 <= len(vehicle_ids) <= 2:
            raise ValueError("backend requires drone.v02.mock and one or two vehicles")
        if len(set(vehicle_ids)) != len(vehicle_ids):
            raise ValueError("vehicle IDs must be unique")
        positions = {}
        for vehicle in vehicle_ids:
            initial = scenario.initial_states.get(vehicle)
            if not isinstance(initial, Mapping):
                raise ValueError(f"missing initial state for {vehicle}")
            north = initial.get("north_m")
            if isinstance(north, bool) or not isinstance(north, (int, float)) or not math.isfinite(north):
                raise ValueError(f"invalid initial north_m for {vehicle}")
            positions[vehicle] = float(north)
        self.positions = positions
        self.remaining_wh = {vehicle: self.battery_capacity_wh for vehicle in vehicle_ids}
        self.sequence = 0
        self.active = True
        return self.observe()

    def execute(self, action: ActionV02) -> ActionOutcome:
        if not self.active or action.vehicle_id not in self.positions:
            return ActionOutcome(action.action_id, action.vehicle_id, False, "unknown vehicle")
        if (action.kind != ACTION_KIND or action.channel is not ActionChannel.CONTROL
                or action.payload_schema != ACTION_SCHEMA):
            return ActionOutcome(action.action_id, action.vehicle_id, False,
                                 "unsupported action")
        target = action.payload.get("north_m")
        if (set(action.payload) != {"north_m"} or isinstance(target, bool)
                or not isinstance(target, (int, float)) or not math.isfinite(target)):
            return ActionOutcome(action.action_id, action.vehicle_id, False,
                                 "move requires a finite north_m")
        vehicle = action.vehicle_id
        delta = float(target) - self.positions[vehicle]
        if delta:
            distance = min(abs(delta), self.max_speed_mps * self.step_duration_s,
                           self.remaining_wh[vehicle] / ENERGY_WH_PER_METER)
            if distance <= 0:
                return ActionOutcome(action.action_id, vehicle, False, "battery depleted")
            self.positions[vehicle] += math.copysign(distance, delta)
            self.remaining_wh[vehicle] = max(
                0.0, self.remaining_wh[vehicle] - distance * ENERGY_WH_PER_METER)
        self.sequence += 1
        return ActionOutcome(action.action_id, vehicle, True)

    def observe(self) -> EpisodeSnapshotV02:
        if self.closed or not self.active:
            raise RuntimeError("backend is not active")
        return EpisodeSnapshotV02(self.sequence, {
            vehicle: PlatformObservationV02(
                self.sequence, vehicle, time.time_ns(),
                {"north_m": north, "east_m": 0.0, "down_m": 0.0,
                 "battery_remaining_wh": self.remaining_wh[vehicle]},
            )
            for vehicle, north in self.positions.items()
        })

    def close(self) -> None:
        self.positions.clear()
        self.remaining_wh.clear()
        self.active = False
        self.closed = True


def create_backend(*, config, context):
    return DesignedMockBackend(config)
