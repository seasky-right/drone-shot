"""Candidate public component boundary for Plugin API v0.2.

Factories receive only public context and validated JSON configuration. Discovery,
manifest parsing, and lifecycle scheduling belong to Core, not to this module.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping, Protocol


PLUGIN_API_VERSION = "drone.plugin.api/v0.2"


class PluginType(str, Enum):
    BACKEND = "backend"
    TASK = "task"
    AGENT = "agent"
    EVALUATOR = "evaluator"
    SCENARIO = "scenario"
    SCENARIO_GENERATOR = "scenario_generator"
    RUNTIME_PROVIDER = "runtime_provider"
    TRAINING_DRIVER = "training_driver"
    RESULT_PROCESSOR = "result_processor"
    BENCHMARK = "benchmark"


class ArtifactWriter(Protocol):
    def write_bytes(self, relative_path: str, content: bytes) -> str: ...


class CoreContext(Protocol):
    """The factory's restricted view; truth is provided only to authorized roles."""

    @property
    def episode_id(self) -> str: ...

    @property
    def artifacts(self) -> ArtifactWriter: ...

    def emit(self, kind: str, fields: Mapping[str, object]) -> None: ...


class ComponentFactory(Protocol):
    """One pack can expose several factories under distinct stable component IDs."""

    def __call__(self, config: Mapping[str, object], context: CoreContext) -> object: ...


class Component(Protocol):
    """Close is the only universal hook; Core owns hook ordering and errors."""

    def close(self) -> None: ...
