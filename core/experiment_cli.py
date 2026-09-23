"""Run repeatable Mock experiments with either stand-ins or ReachPoint consumers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents.fixed_route import FixedRouteAgent as TrackCFixedRouteAgent
from agents.mock import FixedMoveAgent, HoverAgent, OffsetMoveAgent
from agents.reach_point import DirectPointAgent
from agents.reachpoint import FixedRouteAgent as LegacyFixedRouteAgent
from backends.mock import MockBackend
from contracts import PositionNed
from evaluators.mock import OneStepPositionEvaluator
from evaluators.reachpoint import ReachPointEvaluator
from tasks.mock import OneStepPositionTask
from tasks.reachpoint import ReachPointTask
from .experiment import ExperimentManager, ExperimentPlan


def build_manager(plan: ExperimentPlan, output: Path) -> ExperimentManager:
    """Compose task-specific consumers without adding task rules to ExperimentManager."""
    if plan.task_spec.task_type == "mock_one_step_position":
        target = PositionNed.from_dict(plan.task_spec.parameters["target_position_ned"])
        return ExperimentManager(
            output,
            backend_factory=MockBackend,
            task_factory=OneStepPositionTask,
            evaluator_factory=lambda: OneStepPositionEvaluator(target),
            agent_factories={
                "fixed": lambda: FixedMoveAgent(target),
                "hover": HoverAgent,
                "offset": OffsetMoveAgent,
            },
        )
    if plan.task_spec.task_type == "reach_point":
        return ExperimentManager(
            output,
            backend_factory=MockBackend,
            task_factory=ReachPointTask,
            evaluator_factory=lambda: ReachPointEvaluator.from_spec(plan.task_spec),
            agent_factories={
                "direct": DirectPointAgent,
                "fixed-route": TrackCFixedRouteAgent,
                "legacy-route": LegacyFixedRouteAgent,
            },
        )
    raise ValueError(f"unsupported task_type for Mock experiment CLI: {plan.task_spec.task_type}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("experiment.example.json"))
    parser.add_argument("--output", type=Path, default=Path("experiments"))
    args = parser.parse_args(argv)
    plan = ExperimentPlan.from_dict(json.loads(args.config.read_text(encoding="utf-8")))
    if plan.backend_config.backend_type != "mock":
        parser.error("this example CLI only runs MockBackend")
    try:
        manager = build_manager(plan, args.output)
    except ValueError as exc:
        parser.error(str(exc))
    result = manager.run(plan)
    print(json.dumps({
        "experiment_id": result.experiment_id,
        "summary_path": str(result.directory / "summary.json"),
        "report_path": str(result.directory / "report.md"),
        "episode_count": result.summary["episode_count"],
    }, ensure_ascii=False))
    return 1 if any(item["infrastructure_error_count"] for item in result.summary["agents"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
