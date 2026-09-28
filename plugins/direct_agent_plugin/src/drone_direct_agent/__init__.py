"""DirectPointAgent v0.2 candidate plugin.

Adapts direct-point navigation behavior to candidate Plugin API v0.2.
Compatible with built-in Mock Backend, Mock Scenario, and Mock Task.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence, Union

from contracts.data_v02 import (
    ActionChannel,
    ActionV02,
    EpisodeSnapshotV02,
    PlatformObservationV02,
)
from contracts.model import ContractValidationError
from contracts.plugin_v02 import CoreContext

ACTION_KIND = "drone/move"
ACTION_SCHEMA = "drone.move/v1"


class DirectPointAgent:
    """Agent that navigates vehicles along the north axis to a target position, then holds."""

    def __init__(
        self,
        target_north_m: float,
        step_size_m: float | None = None,
        tolerance_m: float = 0.1,
        action_deadline_s: float = 1.0,
    ) -> None:
        if not math.isfinite(target_north_m):
            raise ContractValidationError("target_north_m must be finite")
        if step_size_m is not None:
            if not math.isfinite(step_size_m) or step_size_m <= 0:
                raise ContractValidationError("step_size_m must be a positive finite number")
        if not math.isfinite(tolerance_m) or tolerance_m < 0:
            raise ContractValidationError("tolerance_m must be non-negative and finite")
        if not math.isfinite(action_deadline_s) or action_deadline_s <= 0:
            raise ContractValidationError("action_deadline_s must be a positive finite number")

        self.target_north_m = float(target_north_m)
        self.step_size_m = float(step_size_m) if step_size_m is not None else None
        self.tolerance_m = float(tolerance_m)
        self.action_deadline_s = float(action_deadline_s)
        self.closed = False
        self._action_counter = 0

    def _compute_action(
        self, observation: PlatformObservationV02, sequence: int
    ) -> ActionV02:
        """Compute the next navigation action for an individual vehicle observation."""
        vehicle_id = observation.vehicle_id
        if "north_m" not in observation.state:
            raise ContractValidationError(
                f"vehicle '{vehicle_id}' observation state missing required 'north_m'"
            )
        raw_north = observation.state["north_m"]
        if (
            isinstance(raw_north, bool)
            or not isinstance(raw_north, (int, float))
            or not math.isfinite(raw_north)
        ):
            raise ContractValidationError(
                f"vehicle '{vehicle_id}' observation state 'north_m' must be a finite number, got {raw_north!r}"
            )
        current_north = float(raw_north)
        delta = self.target_north_m - current_north

        if abs(delta) <= self.tolerance_m:
            # Target reached within tolerance; hold position at target
            commanded_north = self.target_north_m
        elif self.step_size_m is not None:
            # Step-by-step movement towards target
            step = math.copysign(min(self.step_size_m, abs(delta)), delta)
            commanded_north = current_north + step
        else:
            # Direct movement to target
            commanded_north = self.target_north_m

        self._action_counter += 1
        action_id = f"direct-{sequence}-{vehicle_id}"
        return ActionV02(
            action_id=action_id,
            vehicle_id=vehicle_id,
            kind=ACTION_KIND,
            channel=ActionChannel.CONTROL,
            payload_schema=ACTION_SCHEMA,
            payload={"north_m": float(commanded_north)},
            deadline_s=self.action_deadline_s,
        )

    def act(
        self, target: Union[EpisodeSnapshotV02, PlatformObservationV02]
    ) -> Union[ActionV02, Sequence[ActionV02]]:
        """Act hook supporting both CentralAgent (snapshot) and VehicleAgent (observation)."""
        if self.closed:
            raise RuntimeError("cannot act on closed agent")

        if isinstance(target, EpisodeSnapshotV02):
            return tuple(
                self._compute_action(obs, target.sequence)
                for obs in target.observations.values()
            )
        elif isinstance(target, PlatformObservationV02):
            return self._compute_action(target, target.sequence)
        else:
            raise ContractValidationError(
                f"unsupported act input type: {type(target).__name__}"
            )

    def close(self) -> None:
        """Cleanup hook."""
        self.closed = True


def create_agent(
    *, config: Mapping[str, object], context: CoreContext | None = None
) -> DirectPointAgent:
    """Factory function matching ComponentFactory protocol."""
    target_north_m = float(config.get("target_north_m", 5.0))
    step_size_m = config.get("step_size_m")
    step_size = float(step_size_m) if step_size_m is not None else None
    tolerance_m = float(config.get("tolerance_m", 0.1))
    deadline_s = float(config.get("action_deadline_s", 1.0))
    return DirectPointAgent(
        target_north_m=target_north_m,
        step_size_m=step_size,
        tolerance_m=tolerance_m,
        action_deadline_s=deadline_s,
    )
