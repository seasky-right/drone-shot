"""A pack that imports only the published platform contract and Core API."""

from __future__ import annotations

import time
from hashlib import sha256
from dataclasses import dataclass
from typing import Mapping

from contracts.data_v02 import (
    ActionChannel, ActionV02, CapabilitySetV02, EpisodeSnapshotV02,
    PlatformObservationV02, ScenarioSpecV02,
)
from core.multi_vehicle import ActionOutcome


def register(*, config=None):
    return None


@dataclass(frozen=True)
class UrbanScenario:
    spec: ScenarioSpecV02
    truth: dict[str, object]

    def close(self):
        return None


def create_scenario(*, config, context):
    target = config.get("target_north_m", 5)
    return UrbanScenario(
        ScenarioSpecV02("sample.urban", "1.0.0", "sample-urban-v1", {},
                        config.get("seed", 0), {"A": {"north_m": 0},
                                                "B": {"north_m": 0}}),
        {"target_north_m": target},
    )


class TwoVehicleMock:
    def __init__(self):
        self.positions: dict[str, float] = {}
        self.sequence = 0

    def capabilities(self):
        return CapabilitySetV02(("sample/move",), (), {}, "local_ned",
                                ("wall",), 2, ("load", "reset"), True, True)

    def reset(self, scenario, vehicle_ids):
        self.positions = {vehicle: float(scenario.initial_states[vehicle]["north_m"])
                          for vehicle in vehicle_ids}
        self.sequence = 0
        return self.observe()

    def observe(self):
        observations = {
            vehicle: PlatformObservationV02(
                self.sequence, vehicle, time.time_ns(),
                {"north_m": north, "east_m": 0.0, "down_m": 0.0},
                extensions={"sample/visited": self.sequence > 0},
            )
            for vehicle, north in self.positions.items()
        }
        return EpisodeSnapshotV02(self.sequence, observations)

    def execute(self, action):
        if action.vehicle_id not in self.positions:
            return ActionOutcome(action.action_id, action.vehicle_id, False, "unknown vehicle")
        if action.kind != "sample/move" or action.payload_schema != "sample.move/v1":
            return ActionOutcome(action.action_id, action.vehicle_id, False, "unknown action")
        self.positions[action.vehicle_id] = float(action.payload["north_m"])
        self.sequence += 1
        return ActionOutcome(action.action_id, action.vehicle_id, True)

    def close(self):
        self.positions.clear()


def create_backend(*, config, context):
    return TwoVehicleMock()


class MockRuntimeProvider:
    def __init__(self, context):
        self.context = context

    def acquire(self, episode_id, request):
        if not request["vehicle_ids"] or request["scenario"]["scenario_id"] != "sample.urban":
            raise ValueError("unexpected runtime request")
        self.context.emit("sample.runtime.acquired", {"episode_id": episode_id})
        return episode_id

    def release(self, resource):
        self.context.emit("sample.runtime.released", {"episode_id": resource})

    def close(self):
        return None


def create_runtime(*, config, context):
    return MockRuntimeProvider(context)


class CentralMoveAgent:
    def __init__(self, target_north_m):
        self.target = target_north_m

    def act(self, snapshot):
        return tuple(
            ActionV02(f"move-{snapshot.sequence}-{vehicle}", vehicle, "sample/move",
                      ActionChannel.CONTROL, "sample.move/v1",
                      {"north_m": self.target}, 1.0)
            for vehicle in snapshot.observations
        )

    def close(self):
        return None


def create_agent(*, config, context):
    context.emit("sample.agent.loaded", {"episode_id": context.episode_id})
    return CentralMoveAgent(config.get("target_north_m", 5))


class SearchTask:
    def complete(self, snapshot, truth):
        target = truth["target_north_m"]
        return all(observation.state["north_m"] >= target
                   for observation in snapshot.observations.values())

    def close(self):
        return None


def create_task(*, config, context):
    return SearchTask()


class MappingTask:
    def complete(self, snapshot, truth):
        del truth
        return all(observation.extensions["sample/visited"]
                   for observation in snapshot.observations.values())

    def close(self):
        return None


def create_mapping_task(*, config, context):
    return MappingTask()


class SearchEvaluator:
    def evaluate(self, result: Mapping[str, object]) -> dict[str, float]:
        return {"sample/success": float(result["success"])}

    def close(self):
        return None


def create_evaluator(*, config, context):
    return SearchEvaluator()


class UrbanScenarioGenerator:
    def generate(self, seed: int) -> ScenarioSpecV02:
        checksum = sha256(f"sample.urban.seed:{seed}".encode("ascii")).hexdigest()
        return ScenarioSpecV02(
            "sample.urban", "1.0.0", checksum, {}, seed,
            {"A": {"north_m": 0}, "B": {"north_m": 0}},
        )

    def close(self):
        return None


def create_scenario_generator(*, config, context):
    return UrbanScenarioGenerator()


class TwoEpisodeTrainingDriver:
    def run(self, session_factory):
        results = []
        for _ in range(2):
            session = session_factory()
            session.reset()
            results.append(session.step())
        return tuple(results)

    def close(self):
        return None


def create_training_driver(*, config, context):
    return TwoEpisodeTrainingDriver()


class SuccessResultProcessor:
    def process(self, read_result, episode_ids):
        results = [read_result(episode_id) for episode_id in episode_ids]
        return {
            "count": len(results),
            "success_count": sum(result["success"] is True for result in results),
        }

    def close(self):
        return None


def create_result_processor(*, config, context):
    return SuccessResultProcessor()


class SearchBenchmark:
    def cases(self):
        return (
            {"case_id": "sample.search.seed-7", "scenario_seed": 7,
             "task": "sample.search/task", "target_north_m": 5},
            {"case_id": "sample.search.seed-11", "scenario_seed": 11,
             "task": "sample.search/task", "target_north_m": 8},
        )

    def close(self):
        return None


def create_benchmark(*, config, context):
    return SearchBenchmark()
