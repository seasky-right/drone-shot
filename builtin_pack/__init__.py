"""Factories for the separately distributed first-party component pack."""

from __future__ import annotations

from contracts import PositionNed


def register(*, config=None):
    """Entry-point identity; discovery reads metadata without calling this."""
    return None


def create_mock_backend(*, config=None, context=None):
    from backends.mock import MockBackend
    return MockBackend()


def create_airsim_backend(*, config=None, context=None):
    from backends.airsim import AirSimBackend
    return AirSimBackend()


def create_reachpoint_task(*, config=None, context=None):
    from tasks.reachpoint import ReachPointTask
    return ReachPointTask()


def create_mock_task(*, config=None, context=None):
    from tasks.mock import OneStepPositionTask
    return OneStepPositionTask()


def create_reachpoint_evaluator(*, config=None, context=None, spec):
    from evaluators.reachpoint import ReachPointEvaluator
    return ReachPointEvaluator.from_spec(spec)


def create_mock_evaluator(*, config=None, context=None, spec):
    from evaluators.mock import OneStepPositionEvaluator
    target = PositionNed.from_dict(spec.parameters["target_position_ned"])
    return OneStepPositionEvaluator(target)


def create_fixed_agent(*, config=None, context=None, spec=None):
    from agents.reachpoint import FixedRouteAgent
    return FixedRouteAgent()


def create_direct_agent(*, config=None, context=None, spec=None):
    from agents.reach_point import DirectPointAgent
    return DirectPointAgent()


def create_fixed_route_agent(*, config=None, context=None, spec=None):
    from agents.fixed_route import FixedRouteAgent
    return FixedRouteAgent()


def create_legacy_route_agent(*, config=None, context=None, spec=None):
    from agents.reachpoint import FixedRouteAgent
    return FixedRouteAgent()


def create_hover_agent(*, config=None, context=None, spec=None):
    from agents.mock import HoverAgent
    return HoverAgent()


def create_mock_fixed_agent(*, config=None, context=None, spec):
    from agents.mock import FixedMoveAgent
    target = PositionNed.from_dict(spec.parameters["target_position_ned"])
    return FixedMoveAgent(target)


def create_offset_agent(*, config=None, context=None, spec=None):
    from agents.mock import OffsetMoveAgent
    return OffsetMoveAgent()
