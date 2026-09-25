from __future__ import annotations

import unittest
import math

from contracts.data_v02 import (
    ActionChannel, ActionV02, AgentBindingV02, CapabilitySetV02,
    EpisodeSnapshotV02, PlatformObservationV02, ScenarioSpecV02,
)
from core.multi_vehicle import (
    ActionOutcome, EpisodeStatus, MultiVehicleEpisode, PreflightError,
)

ACTION_PAYLOAD_SCHEMAS = {
    "drone.control/move_to": {
        "type": "object", "required": ["position"],
        "properties": {"position": {"type": "integer"}},
        "additionalProperties": False,
    }
}


def action(vehicle_id: str, sequence: int) -> ActionV02:
    return ActionV02(f"{vehicle_id}-{sequence}", vehicle_id, "drone.control/move_to",
                     ActionChannel.CONTROL, "drone.action.move_to/v0.2",
                     {"position": sequence + 1}, 1.0)


class FakeBackend:
    def __init__(self, *, fail_vehicle: str | None = None) -> None:
        self.fail_vehicle = fail_vehicle
        self.closed = False
        self.positions: dict[str, int] = {}
        self.sequence = 0
        self.executed: list[str] = []

    def reset(self, scenario: ScenarioSpecV02, vehicle_ids: tuple[str, ...]) -> EpisodeSnapshotV02:
        self.positions = {vehicle: 0 for vehicle in vehicle_ids}
        self.sequence = 0
        return self._snapshot()

    def _snapshot(self) -> EpisodeSnapshotV02:
        return EpisodeSnapshotV02(self.sequence, {
            vehicle: PlatformObservationV02(self.sequence, vehicle, self.sequence,
                                             {"position": position})
            for vehicle, position in self.positions.items()})

    def execute(self, requested: ActionV02) -> ActionOutcome:
        self.executed.append(requested.vehicle_id)
        if requested.vehicle_id == self.fail_vehicle:
            return ActionOutcome(requested.action_id, requested.vehicle_id, False, "scripted failure")
        self.positions[requested.vehicle_id] = requested.payload["position"]
        return ActionOutcome(requested.action_id, requested.vehicle_id, True)

    def observe(self) -> EpisodeSnapshotV02:
        self.sequence += 1
        return self._snapshot()

    def close(self) -> None:
        self.closed = True


class CentralAgent:
    def act(self, snapshot: EpisodeSnapshotV02) -> tuple[ActionV02, ...]:
        assert not hasattr(snapshot, "truth")
        return tuple(action(vehicle, snapshot.sequence) for vehicle in snapshot.observations)


class PerVehicleAgent:
    def act(self, observation: PlatformObservationV02) -> ActionV02:
        assert not hasattr(observation, "truth")
        return action(observation.vehicle_id, observation.sequence)


class WrongSchemaAgent(PerVehicleAgent):
    def act(self, observation: PlatformObservationV02) -> ActionV02:
        requested = super().act(observation)
        return ActionV02(requested.action_id, requested.vehicle_id, requested.kind,
                         requested.channel, "unregistered/v1", requested.payload,
                         requested.deadline_s)


class ReportAgent(PerVehicleAgent):
    def act(self, observation: PlatformObservationV02) -> ActionV02:
        requested = super().act(observation)
        return ActionV02(requested.action_id, requested.vehicle_id, requested.kind,
                         ActionChannel.REPORT, requested.payload_schema, requested.payload,
                         requested.deadline_s)


class MissingVehicleBackend(FakeBackend):
    def observe(self) -> EpisodeSnapshotV02:
        self.sequence += 1
        available = {vehicle: PlatformObservationV02(self.sequence, vehicle, self.sequence,
                                                      {"position": position})
                     for vehicle, position in self.positions.items() if vehicle != "uav-2"}
        return EpisodeSnapshotV02(self.sequence, available, {"uav-2": "telemetry unavailable"})


class ReachBothTask:
    def complete(self, snapshot: EpisodeSnapshotV02, truth: dict[str, object]) -> bool:
        return all(observation.state["position"] >= truth["target"]
                   for observation in snapshot.observations.values())


class FailingCloseTask(ReachBothTask):
    def close(self) -> None:
        raise RuntimeError("task close failed")


def capability(*, max_vehicles: int = 2, sensors: dict[str, str] | None = None,
               truth_access: bool = True) -> CapabilitySetV02:
    return CapabilitySetV02(("drone.control/move_to",), ("drone.sensor/rgb",),
                            sensors or {"front": "drone.sensor/rgb"}, "world_ned",
                            ("wall",), max_vehicles, ("load", "reset"), truth_access, True)


def scenario() -> ScenarioSpecV02:
    return ScenarioSpecV02("urban", "1", "sha256:abc", {}, 7,
                           {"uav-1": {}, "uav-2": {}})


class MultiVehicleV02Tests(unittest.TestCase):
    def test_central_agent_dual_mock_episode_and_truth_isolation(self) -> None:
        backend = FakeBackend()
        episode = MultiVehicleEpisode(backend, capability(), scenario(),
                                      {"central": CentralAgent()},
                                      [AgentBindingV02("central", ("uav-1", "uav-2"))],
                                      ReachBothTask(), vehicle_ids=("uav-1", "uav-2"),
                                      required_sensor_resources={"front": "drone.sensor/rgb"},
                                      action_schemas={"drone.control/move_to": "drone.action.move_to/v0.2"},
                                      payload_schemas=ACTION_PAYLOAD_SCHEMAS,
                                      required_action_kinds=("drone.control/move_to",),
                                      truth={"target": 2})
        result = episode.run(max_steps=3)
        self.assertEqual(result.status, EpisodeStatus.SUCCESS)
        self.assertEqual(len(result.steps), 2)
        self.assertEqual(backend.executed, ["uav-1", "uav-2", "uav-1", "uav-2"])
        self.assertEqual(result.final_snapshot.observations["uav-2"].state["position"], 2)
        self.assertTrue(backend.closed)

    def test_per_vehicle_bindings_and_partial_failure(self) -> None:
        backend = FakeBackend(fail_vehicle="uav-2")
        episode = MultiVehicleEpisode(backend, capability(), scenario(),
                                      {"a": PerVehicleAgent(), "b": PerVehicleAgent()},
                                      [AgentBindingV02("a", ("uav-1",)),
                                       AgentBindingV02("b", ("uav-2",))],
                                      ReachBothTask(), vehicle_ids=("uav-1", "uav-2"),
                                      action_schemas={"drone.control/move_to": "drone.action.move_to/v0.2"},
                                      payload_schemas=ACTION_PAYLOAD_SCHEMAS,
                                      truth={"target": 1})
        result = episode.run(max_steps=2)
        self.assertEqual(result.status, EpisodeStatus.PARTIAL_FAILURE)
        self.assertEqual([outcome.succeeded for outcome in result.steps[0].outcomes], [True, False])
        self.assertEqual(result.final_snapshot.observations["uav-1"].state["position"], 1)
        self.assertEqual(result.final_snapshot.observations["uav-2"].state["position"], 0)
        self.assertTrue(backend.closed)

    def test_preflight_rejects_capacity_resource_truth_and_binding_gaps(self) -> None:
        common = dict(backend=FakeBackend(), scenario=scenario(),
                      agents={"a": PerVehicleAgent()},
                      bindings=[AgentBindingV02("a", ("uav-1",))], task=ReachBothTask(),
                      vehicle_ids=("uav-1", "uav-2"),
                      action_schemas={"drone.control/move_to": "drone.action.move_to/v0.2"},
                      payload_schemas=ACTION_PAYLOAD_SCHEMAS)
        with self.assertRaisesRegex(PreflightError, "capacity"):
            MultiVehicleEpisode(**{**common, "capability": capability(max_vehicles=1)})
        with self.assertRaisesRegex(PreflightError, "sensor resource"):
            MultiVehicleEpisode(**{**common, "capability": capability(),
                                   "required_sensor_resources": {"down": "drone.sensor/rgb"}})
        with self.assertRaisesRegex(PreflightError, "truth channel"):
            MultiVehicleEpisode(**{**common, "capability": capability(truth_access=False),
                                   "truth": {"target": 1}})
        with self.assertRaisesRegex(PreflightError, "agent binding"):
            MultiVehicleEpisode(**{**common, "capability": capability()})
        with self.assertRaisesRegex(PreflightError, "action schema"):
            MultiVehicleEpisode(**{**common, "capability": capability(), "action_schemas": {},
                                   "required_action_kinds": ("drone.control/move_to",)})

    def test_runtime_schema_rejection_and_missing_vehicle_result(self) -> None:
        bindings = [AgentBindingV02("a", ("uav-1",)), AgentBindingV02("b", ("uav-2",))]
        common = dict(capability=capability(), scenario=scenario(), bindings=bindings,
                      task=ReachBothTask(), vehicle_ids=("uav-1", "uav-2"),
                      action_schemas={"drone.control/move_to": "drone.action.move_to/v0.2"},
                      payload_schemas=ACTION_PAYLOAD_SCHEMAS,
                      truth={"target": 1})
        bad = FakeBackend()
        with self.assertRaisesRegex(Exception, "unregistered action payload schema"):
            MultiVehicleEpisode(bad, agents={"a": WrongSchemaAgent(), "b": PerVehicleAgent()},
                                **common).run(max_steps=1)
        self.assertTrue(bad.closed)
        missing = MissingVehicleBackend()
        result = MultiVehicleEpisode(missing, agents={"a": PerVehicleAgent(), "b": PerVehicleAgent()},
                                     **common).run(max_steps=1)
        self.assertEqual(result.status, EpisodeStatus.PARTIAL_FAILURE)
        self.assertEqual(result.final_snapshot.missing_vehicles, {"uav-2": "telemetry unavailable"})
        self.assertTrue(missing.closed)

    def test_interactive_cancel_and_cleanup_error_preserve_outcome(self) -> None:
        common = dict(capability=capability(), scenario=scenario(),
                      agents={"a": PerVehicleAgent(), "b": PerVehicleAgent()},
                      bindings=[AgentBindingV02("a", ("uav-1",)),
                                AgentBindingV02("b", ("uav-2",))],
                      vehicle_ids=("uav-1", "uav-2"),
                      action_schemas={"drone.control/move_to": "drone.action.move_to/v0.2"},
                      payload_schemas=ACTION_PAYLOAD_SCHEMAS,
                      truth={"target": 1})
        cancelled_backend = FakeBackend()
        cancelled = MultiVehicleEpisode(cancelled_backend, task=ReachBothTask(), **common)
        cancelled.reset(max_steps=2, cancelled=lambda: True)
        self.assertEqual(cancelled.step().status, EpisodeStatus.CANCELLED)
        self.assertTrue(cancelled_backend.closed)
        completed = MultiVehicleEpisode(FakeBackend(), task=FailingCloseTask(), **common)
        result = completed.run(max_steps=2)
        self.assertEqual(result.status, EpisodeStatus.SUCCESS)
        self.assertIn("task.close", result.cleanup_errors[0])

        class InterruptedCloseBackend(FakeBackend):
            def close(self) -> None:
                super().close()
                raise KeyboardInterrupt()

        interrupted = MultiVehicleEpisode(InterruptedCloseBackend(), task=ReachBothTask(), **common)
        result = interrupted.run(max_steps=2)
        self.assertEqual(result.status, EpisodeStatus.SUCCESS)
        self.assertIn("backend.close: KeyboardInterrupt", result.cleanup_errors[0])

    def test_action_deadline_is_reported_after_backend_returns(self) -> None:
        clock_value = [0.0]
        class SlowBackend(FakeBackend):
            def execute(self, requested):
                clock_value[0] += 2.0
                return super().execute(requested)
        backend = SlowBackend()
        episode = MultiVehicleEpisode(
            backend, capability(), scenario(),
            {"a": PerVehicleAgent(), "b": PerVehicleAgent()},
            [AgentBindingV02("a", ("uav-1",)), AgentBindingV02("b", ("uav-2",))],
            ReachBothTask(), vehicle_ids=("uav-1", "uav-2"),
            action_schemas={"drone.control/move_to": "drone.action.move_to/v0.2"},
            payload_schemas=ACTION_PAYLOAD_SCHEMAS,
            truth={"target": 1}, clock=lambda: clock_value[0],
        )
        result = episode.run(max_steps=2, time_budget_s=10)
        self.assertEqual(result.status, EpisodeStatus.TIMEOUT)
        self.assertEqual(result.steps[0].outcomes[0].error, "action_deadline_exceeded")
        self.assertEqual(result.steps[0].outcomes[1].error, "skipped_after_timeout")
        self.assertEqual(backend.executed, ["uav-1"])

    def test_nonfinite_budget_and_slow_agent_do_not_dispatch(self) -> None:
        clock_value = [0.0]
        class SlowAgent(PerVehicleAgent):
            def act(self, observation):
                clock_value[0] += 2.0
                return super().act(observation)
        backend = FakeBackend()
        episode = MultiVehicleEpisode(
            backend, capability(), scenario(),
            {"a": SlowAgent(), "b": PerVehicleAgent()},
            [AgentBindingV02("a", ("uav-1",)), AgentBindingV02("b", ("uav-2",))],
            ReachBothTask(), vehicle_ids=("uav-1", "uav-2"),
            action_schemas={"drone.control/move_to": "drone.action.move_to/v0.2"},
            payload_schemas=ACTION_PAYLOAD_SCHEMAS,
            truth={"target": 1}, clock=lambda: clock_value[0],
        )
        for budget in (math.nan, math.inf, -math.inf):
            with self.subTest(budget=budget), self.assertRaisesRegex(ValueError, "finite"):
                episode.reset(max_steps=2, time_budget_s=budget)
        result = episode.run(max_steps=2, time_budget_s=1.0)
        self.assertEqual(result.status, EpisodeStatus.TIMEOUT)
        self.assertEqual(result.steps, ())
        self.assertEqual(backend.executed, [])
        self.assertTrue(backend.closed)

    def test_keyboard_interrupt_during_backend_call_cancels_and_closes(self) -> None:
        class InterruptingBackend(FakeBackend):
            def execute(self, requested):
                raise KeyboardInterrupt()
        backend = InterruptingBackend()
        episode = MultiVehicleEpisode(
            backend, capability(), scenario(),
            {"a": PerVehicleAgent(), "b": PerVehicleAgent()},
            [AgentBindingV02("a", ("uav-1",)), AgentBindingV02("b", ("uav-2",))],
            ReachBothTask(), vehicle_ids=("uav-1", "uav-2"),
            action_schemas={"drone.control/move_to": "drone.action.move_to/v0.2"},
            payload_schemas=ACTION_PAYLOAD_SCHEMAS,
            truth={"target": 1},
        )
        result = episode.run(max_steps=2)
        self.assertEqual(result.status, EpisodeStatus.CANCELLED)
        self.assertTrue(backend.closed)

    def test_report_action_requires_separate_handler(self) -> None:
        backend = FakeBackend()
        episode = MultiVehicleEpisode(
            backend, capability(), scenario(),
            {"a": ReportAgent(), "b": PerVehicleAgent()},
            [AgentBindingV02("a", ("uav-1",)), AgentBindingV02("b", ("uav-2",))],
            ReachBothTask(), vehicle_ids=("uav-1", "uav-2"),
            action_schemas={"drone.control/move_to": "drone.action.move_to/v0.2"},
            payload_schemas=ACTION_PAYLOAD_SCHEMAS,
            truth={"target": 1},
        )
        with self.assertRaisesRegex(Exception, "no report handler"):
            episode.run(max_steps=1)
        self.assertEqual(backend.executed, [])
        self.assertTrue(backend.closed)

    def test_report_handler_is_distinct_from_control_dispatch(self) -> None:
        backend = FakeBackend()
        reported: list[str] = []
        def handle_report(requested):
            reported.append(requested.vehicle_id)
            return ActionOutcome(requested.action_id, requested.vehicle_id, True)
        episode = MultiVehicleEpisode(
            backend, capability(), scenario(),
            {"a": ReportAgent(), "b": PerVehicleAgent()},
            [AgentBindingV02("a", ("uav-1",)), AgentBindingV02("b", ("uav-2",))],
            ReachBothTask(), vehicle_ids=("uav-1", "uav-2"),
            action_schemas={"drone.control/move_to": "drone.action.move_to/v0.2"},
            payload_schemas=ACTION_PAYLOAD_SCHEMAS,
            action_handlers={ActionChannel.REPORT: handle_report},
            truth={"target": 0},
        )
        self.assertEqual(episode.run(max_steps=1).status, EpisodeStatus.SUCCESS)
        self.assertEqual(reported, ["uav-1"])
        self.assertEqual(backend.executed, ["uav-2"])


if __name__ == "__main__":
    unittest.main()
