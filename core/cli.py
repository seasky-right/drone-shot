"""Run one no-simulator P2 episode from a platform config JSON."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents.mock import HoverAgent
from agents.fixed_route import FixedRouteAgent as TrackCFixedRouteAgent
from agents.reach_point import DirectPointAgent
from agents.reachpoint import FixedRouteAgent
from backends.mock import MockBackend
from contracts import BackendConfig, TaskSpec
from core import EpisodeRunner, Recorder
from evaluators.reachpoint import ReachPointEvaluator
from tasks.reachpoint import ReachPointTask


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.example.json"))
    parser.add_argument("--agent", choices=("fixed", "direct", "fixed-route", "hover"), default="fixed")
    parser.add_argument("--output", type=Path, default=Path("runs"))
    args = parser.parse_args(argv)
    data = json.loads(args.config.read_text(encoding="utf-8"))
    spec = TaskSpec.from_dict(data["task_spec"])
    backend_config = BackendConfig.from_dict(data["backend_config"])
    if backend_config.backend_type != "mock":
        parser.error("this CLI currently supports backend_type=mock")
    if spec.task_type != "reach_point":
        parser.error("this CLI currently supports task_type=reach_point")
    agents = {
        "fixed": FixedRouteAgent,
        "direct": DirectPointAgent,
        "fixed-route": TrackCFixedRouteAgent,
        "hover": HoverAgent,
    }
    agent = agents[args.agent]()
    result = EpisodeRunner(MockBackend(), agent, ReachPointTask(),
                           ReachPointEvaluator.from_spec(spec), Recorder(args.output)).run(spec, backend_config)
    print(json.dumps(result.to_dict(), ensure_ascii=False))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
