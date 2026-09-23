"""Run a repeatable Track A experiment with Mock stand-ins only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents.mock import FixedMoveAgent, HoverAgent, OffsetMoveAgent
from backends.mock import MockBackend
from contracts import PositionNed
from evaluators.mock import OneStepPositionEvaluator
from tasks.mock import OneStepPositionTask
from .experiment import ExperimentManager, ExperimentPlan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("experiment.example.json"))
    parser.add_argument("--output", type=Path, default=Path("experiments"))
    args = parser.parse_args(argv)
    plan = ExperimentPlan.from_dict(json.loads(args.config.read_text(encoding="utf-8")))
    if plan.backend_config.backend_type != "mock":
        parser.error("this example CLI only runs MockBackend")
    target = PositionNed.from_dict(plan.task_spec.parameters["target_position_ned"])
    manager = ExperimentManager(
        args.output,
        backend_factory=MockBackend,
        task_factory=OneStepPositionTask,
        evaluator_factory=lambda: OneStepPositionEvaluator(target),
        agent_factories={
            "fixed": lambda: FixedMoveAgent(target),
            "hover": HoverAgent,
            "offset": OffsetMoveAgent,
        },
    )
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
