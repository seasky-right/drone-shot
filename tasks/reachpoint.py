"""ReachPoint task rules over platform observations, independent of any simulator."""
from __future__ import annotations

import math
from dataclasses import dataclass

from contracts import ActionKind, ContractValidationError, PlatformObservation, PositionNed, StepRecord, TaskProgress, TaskSpec, TerminationReason


def _nonnegative(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ContractValidationError(f"{name} must be a finite non-negative number")
    return float(value)


def distance_m(a: PositionNed, b: PositionNed) -> float:
    return math.dist((a.north_m, a.east_m, a.down_m), (b.north_m, b.east_m, b.down_m))


@dataclass(frozen=True)
class ReachPointRules:
    target: PositionNed
    tolerance_m: float
    require_hover_confirmation: bool = False
    max_arrival_speed_mps: float | None = None

    @classmethod
    def from_spec(cls, spec: TaskSpec) -> "ReachPointRules":
        if spec.task_type != "reach_point":
            raise ContractValidationError("ReachPointTask requires task_type=reach_point")
        params = spec.parameters
        if "target_position_ned" not in params:
            raise ContractValidationError("reach_point requires target_position_ned")
        target = PositionNed.from_dict(params["target_position_ned"])
        tolerance = _nonnegative(params.get("tolerance_m", 0.5), "tolerance_m")
        hover = params.get("require_hover_confirmation", False)
        if not isinstance(hover, bool):
            raise ContractValidationError("require_hover_confirmation must be boolean")
        speed = params.get("max_arrival_speed_mps")
        if speed is not None:
            speed = _nonnegative(speed, "max_arrival_speed_mps")
        return cls(target, tolerance, hover, speed)


class ReachPointTask:
    def __init__(self) -> None:
        self._rules: ReachPointRules | None = None
        self._vehicle_id: str | None = None

    def reset(self, spec: TaskSpec, initial_observation: PlatformObservation) -> None:
        self._rules = ReachPointRules.from_spec(spec)
        self._vehicle_id = initial_observation.vehicle_id

    def update(self, step: StepRecord) -> TaskProgress:
        if self._rules is None or self._vehicle_id is None:
            raise RuntimeError("ReachPointTask must be reset")
        if step.observation_after.vehicle_id != self._vehicle_id:
            raise ContractValidationError("step vehicle does not match task vehicle")
        if not step.execution.succeeded:
            return TaskProgress(True, False, TerminationReason.BACKEND_ERROR)
        if distance_m(step.observation_after.position_ned, self._rules.target) > self._rules.tolerance_m:
            return TaskProgress(False, False)
        if self._rules.max_arrival_speed_mps is not None:
            if math.dist(step.observation_after.velocity_ned_mps, (0.0, 0.0, 0.0)) > self._rules.max_arrival_speed_mps:
                return TaskProgress(False, False)
        if self._rules.require_hover_confirmation and step.action.kind is not ActionKind.HOVER:
            return TaskProgress(False, False)
        return TaskProgress(True, True, TerminationReason.SUCCESS)

    def close(self) -> None:
        self._rules = None
        self._vehicle_id = None
