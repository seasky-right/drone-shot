"""Small first-party v0.2 components for executable contract verification."""

from __future__ import annotations

import time
from hashlib import sha256
from typing import Mapping

from contracts.data_v02 import (
    ActionChannel, ActionV02, CapabilitySetV02, EpisodeSnapshotV02,
    PlatformObservationV02, ScenarioSpecV02,
)
from core.multi_vehicle import ActionOutcome


SCENARIO_ID = "drone.v02.mock"
ACTION_KIND = "drone/move"
ACTION_SCHEMA = "drone.move/v1"


class MockBackend:
    def __init__(self) -> None:
        self.positions: dict[str, float] = {}
        self.sequence = 0
        self.closed = False

    def capabilities(self) -> CapabilitySetV02:
        return CapabilitySetV02((ACTION_KIND,), (), {}, "local_ned", ("wall",),
                                2, ("load", "reset"), True, True)

    def reset(self, scenario: ScenarioSpecV02,
              vehicle_ids: tuple[str, ...]) -> EpisodeSnapshotV02:
        self.positions = {vehicle: float(scenario.initial_states[vehicle]["north_m"])
                          for vehicle in vehicle_ids}
        self.sequence = 0
        return self.observe()

    def execute(self, action: ActionV02) -> ActionOutcome:
        if action.vehicle_id not in self.positions:
            return ActionOutcome(action.action_id, action.vehicle_id, False,
                                 "unknown vehicle")
        if (action.kind != ACTION_KIND or action.channel != ActionChannel.CONTROL
                or action.payload_schema != ACTION_SCHEMA):
            return ActionOutcome(action.action_id, action.vehicle_id, False,
                                 "unsupported action")
        self.positions[action.vehicle_id] = float(action.payload["north_m"])
        self.sequence += 1
        return ActionOutcome(action.action_id, action.vehicle_id, True)

    def observe(self) -> EpisodeSnapshotV02:
        return EpisodeSnapshotV02(self.sequence, {
            vehicle: PlatformObservationV02(
                self.sequence, vehicle, time.time_ns(),
                {"north_m": north, "east_m": 0.0, "down_m": 0.0},
            )
            for vehicle, north in self.positions.items()
        })

    def close(self) -> None:
        self.positions.clear()
        self.closed = True


class ReachTask:
    def complete(self, snapshot: EpisodeSnapshotV02,
                 truth: Mapping[str, object]) -> bool:
        target = float(truth["target_north_m"])
        return all(float(observation.state["north_m"]) >= target
                   for observation in snapshot.observations.values())

    def close(self) -> None:
        pass


class MoveAgent:
    def __init__(self, target: float) -> None:
        self.target = target

    def act(self, snapshot: EpisodeSnapshotV02) -> tuple[ActionV02, ...]:
        return tuple(ActionV02(
            f"move-{snapshot.sequence}-{vehicle}", vehicle, ACTION_KIND,
            ActionChannel.CONTROL, ACTION_SCHEMA, {"north_m": self.target}, 1.0,
        ) for vehicle in snapshot.observations)

    def close(self) -> None:
        pass


class ReachEvaluator:
    def evaluate(self, result: Mapping[str, object]) -> dict[str, float]:
        return {"drone/success": float(result["success"] is True)}

    def close(self) -> None:
        pass


class Scenario:
    def __init__(self, seed: int, target: float) -> None:
        self.spec = scenario_spec(seed)
        self.truth = {"target_north_m": target}

    def close(self) -> None:
        pass


def scenario_spec(seed: int) -> ScenarioSpecV02:
    checksum = sha256(f"{SCENARIO_ID}:{seed}".encode("ascii")).hexdigest()
    return ScenarioSpecV02(SCENARIO_ID, "1.0.0", checksum, {}, seed,
                           {"A": {"north_m": 0}, "B": {"north_m": 0}})


class ScenarioGenerator:
    def generate(self, seed: int) -> Scenario:
        return Scenario(seed, 3 + seed % 3)

    def close(self) -> None:
        pass


class RuntimeProvider:
    def __init__(self) -> None:
        self.acquired: set[str] = set()

    def acquire(self, episode_id: str, request: Mapping[str, object]) -> str:
        if not request["vehicle_ids"]:
            raise ValueError("runtime request has no vehicles")
        self.acquired.add(episode_id)
        return episode_id

    def release(self, resource: str) -> None:
        self.acquired.remove(resource)

    def close(self) -> None:
        if self.acquired:
            raise RuntimeError("runtime resources remain acquired")


class TrainingDriver:
    def run(self, session_factory):
        results = []
        for _ in range(2):
            session = session_factory()
            session.reset()
            results.append(session.step())
        return results

    def close(self) -> None:
        pass


class ResultProcessor:
    def process(self, read_result, episode_ids):
        results = [read_result(episode_id) for episode_id in episode_ids]
        return {"count": len(results),
                "success_count": sum(result["success"] is True for result in results)}

    def close(self) -> None:
        pass


class Benchmark:
    def cases(self):
        return (
            {"case_id": "drone.mock.seed-7", "scenario_seed": 7,
             "task": "drone.v02.mock/task"},
            {"case_id": "drone.mock.seed-11", "scenario_seed": 11,
             "task": "drone.v02.mock/task"},
        )

    def close(self) -> None:
        pass


def create_backend(*, config, context):
    return MockBackend()


def create_task(*, config, context):
    return ReachTask()


def create_agent(*, config, context):
    return MoveAgent(float(config.get("target_north_m", 5)))


def create_evaluator(*, config, context):
    return ReachEvaluator()


def create_scenario(*, config, context):
    return Scenario(config.get("seed", 7), float(config.get("target_north_m", 5)))


def create_generator(*, config, context):
    return ScenarioGenerator()


def create_runtime(*, config, context):
    return RuntimeProvider()


def create_driver(*, config, context):
    return TrainingDriver()


def create_processor(*, config, context):
    return ResultProcessor()


def create_benchmark(*, config, context):
    return Benchmark()
