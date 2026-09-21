"""Simulator-independent command and observation contracts.

All values exposed here use SI units. World coordinates use NED (north, east,
down); vehicle body coordinates use FRD (forward, right, down).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Protocol, Sequence


SCHEMA_VERSION = "drone.simulator.contract/v1"


class ErrorCode(str, Enum):
    NOT_CONNECTED = "not_connected"
    NOT_SUPPORTED = "not_supported"
    INVALID_ARGUMENT = "invalid_argument"
    PRECONDITION_FAILED = "precondition_failed"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    SIMULATOR = "simulator"
    INTERNAL = "internal"


@dataclass(frozen=True)
class ContractError:
    code: ErrorCode
    message: str
    retryable: bool = False
    details: Mapping[str, str] = field(default_factory=dict)


class SimulatorContractException(RuntimeError):
    """Raised for failed reads when no result envelope exists for the value."""

    def __init__(self, error: ContractError) -> None:
        super().__init__(error.message)
        self.error = error


class Capability(str, Enum):
    API_CONTROL = "api_control"
    ARM_DISARM = "arm_disarm"
    TAKEOFF = "takeoff"
    LAND = "land"
    HOVER = "hover"
    VELOCITY_BODY = "velocity_body"
    STATE = "state"
    RGB_CAMERA = "rgb_camera"
    DEPTH_CAMERA = "depth_camera"
    POINT_CLOUD = "point_cloud"


class SensorKind(str, Enum):
    RGB = "rgb"
    DEPTH = "depth"
    SEGMENTATION = "segmentation"
    POINT_CLOUD = "point_cloud"


@dataclass(frozen=True)
class SensorDescriptor:
    """A sensor resource confirmed to be available for one vehicle."""

    sensor_id: str
    kind: SensorKind
    frame_id: str


@dataclass(frozen=True)
class PoseNed:
    """Position in meters and unit quaternion (w, x, y, z) in NED."""

    north_m: float
    east_m: float
    down_m: float
    orientation_w: float = 1.0
    orientation_x: float = 0.0
    orientation_y: float = 0.0
    orientation_z: float = 0.0


@dataclass(frozen=True)
class VehicleState:
    schema: str
    simulator_time_ns: int | None
    wall_time_ns: int
    vehicle_id: str
    pose_ned: PoseNed
    velocity_ned_mps: tuple[float, float, float]
    armed: bool | None
    api_control: bool | None
    landed: bool | None


@dataclass(frozen=True)
class SensorRequest:
    sensor_id: str
    kind: SensorKind
    include_payload: bool = True


@dataclass(frozen=True)
class ObservationMetadata:
    schema: str
    simulator_time_ns: int | None
    wall_time_ns: int
    vehicle_id: str
    sensor_id: str
    kind: SensorKind
    frame_id: str
    encoding: str
    width_px: int | None = None
    height_px: int | None = None
    point_count: int | None = None
    pose_ned: PoseNed | None = None
    attributes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    """Metadata travels independently from an optional binary payload.

    Callers persist or stream ``payload`` as bytes; they must not JSON-encode
    image or point-cloud data into the metadata document.
    """

    metadata: ObservationMetadata
    payload: bytes | None = None


@dataclass(frozen=True)
class CommandResult:
    schema: str
    command_id: str
    operation: str
    accepted: bool
    completed: bool
    simulator_time_ns: int | None
    wall_time_ns: int
    error: ContractError | None = None


class SimulatorSession(Protocol):
    """Session lifecycle and capability discovery; no vehicle operations."""

    @property
    def simulator_id(self) -> str: ...

    @property
    def declared_capabilities(self) -> frozenset[Capability]: ...

    def connect(self, timeout_s: float = 5.0) -> CommandResult: ...

    def close(self) -> None: ...

    def capabilities(self, vehicle_id: str) -> frozenset[Capability]: ...

    def sensors(self, vehicle_id: str) -> tuple[SensorDescriptor, ...]: ...


class VehicleControl(Protocol):
    """Commands are bounded requests; observations are intentionally separate."""

    def state(self, vehicle_id: str) -> VehicleState: ...

    def set_api_control(self, vehicle_id: str, enabled: bool) -> CommandResult: ...

    def arm(self, vehicle_id: str, armed: bool) -> CommandResult: ...

    def takeoff(self, vehicle_id: str, timeout_s: float) -> CommandResult: ...

    def land(self, vehicle_id: str, timeout_s: float) -> CommandResult: ...

    def hover(self, vehicle_id: str) -> CommandResult: ...

    def move_body_velocity(
        self,
        vehicle_id: str,
        forward_mps: float,
        right_mps: float,
        down_mps: float,
        duration_s: float,
    ) -> CommandResult: ...


class SensorReader(Protocol):
    def observe(self, vehicle_id: str, request: SensorRequest) -> Observation: ...


class DroneSimulator(SimulatorSession, VehicleControl, SensorReader, Protocol):
    """Convenience composition for consumers that need all three boundaries."""


def ensure_capabilities(
    available: Sequence[Capability], required: Sequence[Capability]
) -> ContractError | None:
    missing = sorted(set(required) - set(available), key=str)
    if not missing:
        return None
    return ContractError(
        ErrorCode.NOT_SUPPORTED,
        "Simulator does not support required capabilities.",
        details={"missing": ",".join(capability.value for capability in missing)},
    )
