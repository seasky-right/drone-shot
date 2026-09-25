"""Executable, opt-in probes for the candidate plugin component types."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from contracts.data_v02 import (
    ActionChannel, ActionV02, CapabilitySetV02, EpisodeSnapshotV02,
    PlatformObservationV02, ScenarioSpecV02,
)

from .multi_vehicle import ActionOutcome
from .store import EpisodeStore


class ProbeFailure(ValueError):
    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(reason)
        self.stage = stage


def _require(condition: bool, stage: str, reason: str) -> None:
    if not condition:
        raise ProbeFailure(stage, reason)


def _json(value: object, stage: str) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ProbeFailure(stage, "return value is not JSON-safe") from exc


def _scenario(probe: Mapping[str, object]) -> ScenarioSpecV02:
    supplied = probe.get("scenario")
    if supplied is not None:
        return ScenarioSpecV02.from_dict(supplied)
    return ScenarioSpecV02(
        "sample.urban", "1.0.0", "conformance-urban", {}, 7,
        {"A": {"north_m": 0}, "B": {"north_m": 0}},
    )


def _snapshot(*, reached: bool = False) -> EpisodeSnapshotV02:
    sequence = int(reached)
    return EpisodeSnapshotV02(sequence, {
        vehicle: PlatformObservationV02(
            sequence, vehicle, 1 + sequence,
            {"north_m": 5 if reached else 0, "east_m": 0, "down_m": 0},
            extensions={"sample/visited": reached},
        )
        for vehicle in ("A", "B")
    })


def _action(probe: Mapping[str, object], declared: Mapping[str, object]) -> ActionV02:
    if "action" in probe:
        return ActionV02.from_dict(probe["action"])
    kinds = declared.get("action_kinds", ())
    kind = kinds[0] if kinds else "sample/move"
    return ActionV02("conformance-action", "A", kind, ActionChannel.CONTROL,
                     "sample.move/v1", {"north_m": 5}, 1.0)


def _runtime_capabilities(declared: Mapping[str, object],
                          actual: CapabilitySetV02) -> None:
    for field in ("action_kinds", "sensor_types", "time_bases", "scenario_operations"):
        missing = set(declared.get(field, ())) - set(getattr(actual, field))
        _require(not missing, "capabilities",
                 f"declared {field} unavailable at runtime: {sorted(missing)}")
    for resource_id, kind in declared.get("sensor_resources", {}).items():
        _require(actual.sensor_resources.get(resource_id) == kind, "capabilities",
                 f"declared sensor resource unavailable at runtime: {resource_id}")
    for field in ("coordinate_frame",):
        if field in declared:
            _require(getattr(actual, field) == declared[field], "capabilities",
                     f"declared {field} differs from runtime")
    if "max_vehicles" in declared:
        _require(actual.max_vehicles >= declared["max_vehicles"], "capabilities",
                 "declared vehicle capacity exceeds runtime capacity")
    for field in ("truth_access", "bounded_execution"):
        if declared.get(field) is True:
            _require(getattr(actual, field) is True, "capabilities",
                     f"declared {field} is unavailable at runtime")


def _backend(component: object, declared: Mapping[str, object],
             probe: Mapping[str, object]) -> list[str]:
    for hook in ("capabilities", "reset", "execute", "observe"):
        _require(callable(getattr(component, hook, None)), "hooks",
                 f"backend.{hook} is missing")
    actual = component.capabilities()
    _require(isinstance(actual, CapabilitySetV02), "capabilities",
             "backend.capabilities must return CapabilitySetV02")
    _runtime_capabilities(declared, actual)
    scenario = _scenario(probe)
    vehicles = ("A", "B")[:actual.max_vehicles]
    initial = component.reset(scenario, vehicles)
    _require(isinstance(initial, EpisodeSnapshotV02), "reset",
             "backend.reset must return EpisodeSnapshotV02")
    _require(set(initial.observations) | set(initial.missing_vehicles) == set(vehicles),
             "reset", "backend reset vehicle IDs differ from request")
    action = _action(probe, declared)
    if action.vehicle_id not in vehicles:
        action = ActionV02(action.action_id, vehicles[0], action.kind, action.channel,
                           action.payload_schema, action.payload, action.deadline_s)
    _require(action.kind in actual.action_kinds, "execute",
             "probe action is unavailable at runtime")
    outcome = component.execute(action)
    _require(isinstance(outcome, ActionOutcome), "execute",
             "backend.execute must return ActionOutcome")
    _require(outcome.action_id == action.action_id and
             outcome.vehicle_id == action.vehicle_id and outcome.succeeded,
             "execute", f"backend did not complete declared action: {outcome.error}")
    observed = component.observe()
    _require(isinstance(observed, EpisodeSnapshotV02), "observe",
             "backend.observe must return EpisodeSnapshotV02")
    _require(set(observed.observations) | set(observed.missing_vehicles) == set(vehicles),
             "observe", "backend observed vehicle IDs differ from request")
    return ["hooks", "runtime_capabilities", "reset", "execute", "observe"]


def _task(component: object, probe: Mapping[str, object]) -> list[str]:
    _require(callable(getattr(component, "complete", None)), "hooks",
             "task.complete is missing")
    truth = probe.get("truth", {"target_north_m": 5})
    _require(isinstance(truth, Mapping), "truth", "task truth must be a mapping")
    initial = (EpisodeSnapshotV02.from_dict(probe["before_snapshot"])
               if "before_snapshot" in probe else _snapshot())
    final = (EpisodeSnapshotV02.from_dict(probe["after_snapshot"])
             if "after_snapshot" in probe else _snapshot(reached=True))
    before = component.complete(initial, truth)
    after = component.complete(final, truth)
    _require(type(before) is bool and type(after) is bool, "complete",
             "task.complete must return bool for both observations")
    return ["hooks", "truth_channel", "complete"]


def _agent(component: object, declared: Mapping[str, object],
           probe: Mapping[str, object]) -> list[str]:
    _require(callable(getattr(component, "act", None)), "hooks",
             "agent.act is missing")
    snapshot = (EpisodeSnapshotV02.from_dict(probe["snapshot"])
                if "snapshot" in probe else _snapshot())
    _require(all("truth" not in observation.to_dict() and
                 "ground_truth" not in observation.to_dict()
                 for observation in snapshot.observations.values()),
             "truth", "truth appeared in the Agent observation")
    supplied = (snapshot.observations["A"] if probe.get("mode") == "vehicle"
                else snapshot)
    produced = component.act(supplied)
    actions = (produced,) if isinstance(produced, ActionV02) else produced
    _require(isinstance(actions, Sequence) and bool(actions), "act",
             "agent.act must return a nonempty action sequence")
    _require(all(isinstance(item, ActionV02) for item in actions), "act",
             "agent.act returned a non-ActionV02 value")
    _require(all(item.vehicle_id in snapshot.observations for item in actions), "act",
             "agent action targets an unknown vehicle")
    declared_kinds = set(declared.get("action_kinds", ()))
    if declared_kinds:
        _require(all(item.kind in declared_kinds for item in actions), "capabilities",
                 "agent returned an action kind absent from its manifest")
    _require(len({item.action_id for item in actions}) == len(actions), "act",
             "agent returned duplicate action IDs")
    return ["hooks", "truth_isolation", "actions", "declared_action_kinds"]


def _evaluator(component: object, probe: Mapping[str, object]) -> list[str]:
    _require(callable(getattr(component, "evaluate", None)), "hooks",
             "evaluator.evaluate is missing")
    result = dict(probe.get("result", {
        "episode_id": "conformance", "status": "success",
        "success": True, "step_count": 1,
    }))
    _json(result, "evaluate")
    original = json.loads(json.dumps(result))
    metrics = component.evaluate(result)
    _require(isinstance(metrics, Mapping) and bool(metrics), "evaluate",
             "evaluator.evaluate must return a nonempty metric mapping")
    _require(all(isinstance(key, str) and key and
                 isinstance(value, (int, float)) and not isinstance(value, bool) and
                 math.isfinite(value) for key, value in metrics.items()),
             "evaluate", "evaluator metrics must be finite numbers")
    _require(result == original, "evaluate",
             "evaluator changed its input result")
    _json(metrics, "evaluate")
    return ["hooks", "metrics", "input_unchanged"]


def _scenario_component(component: object,
                        declared: Mapping[str, object]) -> list[str]:
    spec = getattr(component, "spec", component)
    _require(isinstance(spec, ScenarioSpecV02), "scenario",
             "scenario must provide ScenarioSpecV02 or .spec")
    ScenarioSpecV02.from_dict(spec.to_dict())
    if "scenario_id" in declared:
        _require(spec.scenario_id == declared["scenario_id"], "capabilities",
                 "declared scenario_id differs from runtime")
    truth = getattr(component, "truth", {})
    _require(isinstance(truth, Mapping), "truth",
             "scenario truth must be a mapping")
    _json(truth, "truth")
    _require(spec.truth_access != "none" or not truth, "truth",
             "scenario with truth_access=none exposed truth")
    return ["scenario_schema", "declared_scenario", "truth_channel"]


def _generator(component: object, declared: Mapping[str, object],
               probe: Mapping[str, object]) -> list[str]:
    _require(callable(getattr(component, "generate", None)), "hooks",
             "scenario_generator.generate is missing")
    seed = probe.get("seed", 7)
    _require(isinstance(seed, int) and not isinstance(seed, bool) and seed >= 0,
             "case", "generator probe seed must be a nonnegative integer")
    generated: list[object] = []
    try:
        for _ in range(2):
            generated.append(component.generate(seed))
        specs = [getattr(item, "spec", item) for item in generated]
        _require(all(isinstance(item, ScenarioSpecV02) for item in specs),
                 "generate", "generate must return ScenarioSpecV02 or .spec")
        _require(all(item.seed == seed for item in specs), "generate",
                 "generated scenario did not record requested seed")
        if "scenario_id" in declared:
            _require(all(item.scenario_id == declared["scenario_id"] for item in specs),
                     "capabilities",
                     "declared scenario_id differs from generated scenario")
        _require(specs[0].to_dict() == specs[1].to_dict(), "generate",
                 "same seed produced different scenario metadata")
        return ["hooks", "scenario_schema", "seed", "repeatability"]
    finally:
        for item in generated:
            close = getattr(item, "close", None)
            if callable(close) and item is not component:
                close()


def _runtime(component: object, probe: Mapping[str, object]) -> list[str]:
    for hook in ("acquire", "release"):
        _require(callable(getattr(component, hook, None)), "hooks",
                 f"runtime_provider.{hook} is missing")
    request = probe.get("request", {
        "vehicle_ids": ["A", "B"], "scenario": _scenario({}).to_dict(),
    })
    _require(isinstance(request, Mapping), "case",
             "runtime probe request must be a mapping")
    resource = component.acquire("conformance", request)
    _require(resource is not None, "acquire", "runtime.acquire returned no resource")
    component.release(resource)
    return ["hooks", "acquire", "release"]


class _ProbeSession:
    def __init__(self, number: int) -> None:
        self.number = number
        self.reset_count = 0
        self.step_count = 0

    def reset(self) -> dict[str, object]:
        self.reset_count += 1
        return {"episode_id": f"probe-{self.number}"}

    def step(self) -> dict[str, object]:
        self.step_count += 1
        return {"episode_id": f"probe-{self.number}", "status": "success"}


def _driver(component: object) -> list[str]:
    _require(callable(getattr(component, "run", None)), "hooks",
             "training_driver.run is missing")
    sessions: list[_ProbeSession] = []

    def factory() -> _ProbeSession:
        session = _ProbeSession(len(sessions))
        sessions.append(session)
        return session

    results = component.run(factory)
    _require(isinstance(results, Sequence) and len(results) >= 2, "run",
             "training driver must return at least two episode results")
    _require(len(sessions) >= 2 and all(s.reset_count >= 1 and s.step_count >= 1
                                        for s in sessions), "run",
             "training driver did not reset and step distinct sessions")
    _json(results, "run")
    return ["hooks", "repeated_reset_step", "results"]


def _processor(component: object, probe: Mapping[str, object]) -> list[str]:
    _require(callable(getattr(component, "process", None)), "hooks",
             "result_processor.process is missing")
    with TemporaryDirectory(prefix="drone-result-probe-") as temporary:
        store = EpisodeStore(Path(temporary))
        store.start("probe-result")
        result = probe.get("result", {
            "episode_id": "probe-result", "status": "success", "success": True,
        })
        _require(isinstance(result, Mapping), "case",
                 "processor probe result must be a mapping")
        store.finish(dict(result))
        original = EpisodeStore.read_result(temporary, "probe-result")

        def read_result(episode_id: str) -> dict[str, object]:
            return EpisodeStore.read_result(temporary, episode_id)

        summary = component.process(read_result, ("probe-result",))
        _require(isinstance(summary, Mapping), "process",
                 "result_processor.process must return a mapping")
        _json(summary, "process")
        _require(EpisodeStore.read_result(temporary, "probe-result") == original,
                 "process", "result processor modified the stored result")
    return ["hooks", "read_only_result", "summary"]


def _benchmark(component: object) -> list[str]:
    _require(callable(getattr(component, "cases", None)), "hooks",
             "benchmark.cases is missing")
    cases = component.cases()
    _require(isinstance(cases, Sequence) and bool(cases), "cases",
             "benchmark.cases must return a nonempty sequence")
    _require(all(isinstance(item, Mapping) and
                 isinstance(item.get("case_id"), str) and item["case_id"]
                 for item in cases), "cases",
             "benchmark cases require nonempty case_id")
    _require(len({item["case_id"] for item in cases}) == len(cases), "cases",
             "benchmark case IDs must be unique")
    _json(cases, "cases")
    return ["hooks", "cases", "unique_ids"]


def exercise_component(kind: str, component: object,
                       declared: Mapping[str, object],
                       probe: Mapping[str, object]) -> list[str]:
    checks: dict[str, Callable[[], list[str]]] = {
        "backend": lambda: _backend(component, declared, probe),
        "task": lambda: _task(component, probe),
        "agent": lambda: _agent(component, declared, probe),
        "evaluator": lambda: _evaluator(component, probe),
        "scenario": lambda: _scenario_component(component, declared),
        "scenario_generator": lambda: _generator(component, declared, probe),
        "runtime_provider": lambda: _runtime(component, probe),
        "training_driver": lambda: _driver(component),
        "result_processor": lambda: _processor(component, probe),
        "benchmark": lambda: _benchmark(component),
    }
    if kind not in checks:
        raise ProbeFailure("type", f"unsupported component type: {kind}")
    return checks[kind]()
