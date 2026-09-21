"""Public platform contracts, distinct from simulator_contract's RPC boundary."""

from .interfaces import Agent, Backend, Evaluator, Task
from .lifecycle import time_budget_exhausted
from .model import (
    PLATFORM_SCHEMA_VERSION, Action, ActionKind, BackendConfig, CleanupResult,
    ContractError, ContractValidationError, PlatformContractException, EpisodeEvent, EpisodeResult,
    EventSource, ExecutionResult, PlatformObservation, PositionNed,
    SensorReference, StepRecord, TaskProgress, TaskSpec, TerminationReason,
)

__all__ = ["PLATFORM_SCHEMA_VERSION", "Action", "ActionKind", "Agent", "Backend", "BackendConfig", "CleanupResult", "ContractError", "ContractValidationError", "PlatformContractException", "EpisodeEvent", "EpisodeResult", "Evaluator", "EventSource", "ExecutionResult", "PlatformObservation", "PositionNed", "SensorReference", "StepRecord", "Task", "TaskProgress", "TaskSpec", "TerminationReason", "time_budget_exhausted"]
