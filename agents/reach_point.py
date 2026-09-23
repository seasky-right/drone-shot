"""Formal DirectPointAgent for reach_point tasks under platform contract v0.1."""
from __future__ import annotations

import math
from typing import Mapping

from contracts import (
    Action,
    ActionKind,
    ContractValidationError,
    PlatformObservation,
    PositionNed,
    TaskSpec,
)


class DirectPointAgent:
    """Agent that commands a drone directly to a target NED position, then hovers."""

    def __init__(self) -> None:
        self._target_position_ned: PositionNed | None = None
        self._tolerance_m: float = 0.5
        self._action_deadline_s: float = 5.0
        self._vehicle_id: str | None = None
        self._action_count: int = 0

    def reset(self, task: TaskSpec, initial_observation: PlatformObservation) -> None:
        """Initialize the agent from TaskSpec and initial observation.

        Guarantees atomicity: if reset fails for any reason, existing state is cleared
        and the agent remains in an uninitialized state.
        """
        # Clear existing state immediately to prevent hybrid state on validation failure
        self.close()

        if task.task_type != "reach_point":
            raise ContractValidationError(
                f"task_type must be reach_point, got '{task.task_type}'"
            )

        params = task.parameters
        if not isinstance(params, Mapping):
            raise ContractValidationError("task parameters must be an object")

        if "target_position_ned" not in params:
            raise ContractValidationError(
                "task parameters missing required 'target_position_ned'"
            )

        target_position_ned = PositionNed.from_dict(params["target_position_ned"])

        if "tolerance_m" in params:
            tol = params["tolerance_m"]
            if (
                isinstance(tol, bool)
                or not isinstance(tol, (int, float))
                or not math.isfinite(tol)
                or tol <= 0
            ):
                raise ContractValidationError(
                    "tolerance_m must be a positive finite number"
                )
            tolerance_m = float(tol)
        else:
            tolerance_m = 0.5

        if "action_deadline_s" in params:
            dl = params["action_deadline_s"]
            if (
                isinstance(dl, bool)
                or not isinstance(dl, (int, float))
                or not math.isfinite(dl)
                or dl <= 0
            ):
                raise ContractValidationError(
                    "action_deadline_s must be a positive finite number"
                )
            action_deadline_s = float(dl)
        else:
            action_deadline_s = 5.0

        vehicle_id = initial_observation.vehicle_id

        # Commit validated state atomically
        self._target_position_ned = target_position_ned
        self._tolerance_m = tolerance_m
        self._action_deadline_s = action_deadline_s
        self._vehicle_id = vehicle_id
        self._action_count = 0

    def act(self, observation: PlatformObservation) -> Action:
        """Produce next action based on current observation."""
        if self._vehicle_id is None or self._target_position_ned is None:
            raise RuntimeError("agent must be reset before calling act()")

        if observation.vehicle_id != self._vehicle_id:
            raise ContractValidationError(
                f"observation vehicle_id '{observation.vehicle_id}' "
                f"does not match agent vehicle_id '{self._vehicle_id}'"
            )

        self._action_count += 1
        action_id = f"direct-point-{self._action_count}"

        pos = observation.position_ned
        tgt = self._target_position_ned
        distance = math.dist(
            (pos.north_m, pos.east_m, pos.down_m),
            (tgt.north_m, tgt.east_m, tgt.down_m),
        )

        if distance > self._tolerance_m:
            return Action(
                action_id=action_id,
                kind=ActionKind.MOVE_TO,
                vehicle_id=self._vehicle_id,
                deadline_s=self._action_deadline_s,
                target_position_ned=tgt,
            )
        else:
            return Action(
                action_id=action_id,
                kind=ActionKind.HOVER,
                vehicle_id=self._vehicle_id,
                deadline_s=self._action_deadline_s,
                target_position_ned=None,
            )

    def close(self) -> None:
        """Release episode state."""
        self._target_position_ned = None
        self._vehicle_id = None
        self._action_count = 0
