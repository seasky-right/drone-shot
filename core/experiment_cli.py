"""Run repeatable experiments with installed plugin components."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .assembly import _legacy_component, _selection, discover_plugins
from .experiment import ExperimentManager, ExperimentPlan
from .plugins import PluginRegistryError


def build_manager(plan: ExperimentPlan, output: Path) -> ExperimentManager:
    registry = discover_plugins()
    if plan.components is None:
        selected = {
            "backend": (_legacy_component(registry, "backend", "legacy_backend_type",
                                          plan.backend_config.backend_type), {}),
            "task": (_legacy_component(registry, "task", "legacy_task_type",
                                       plan.task_spec.task_type), {}),
            "evaluator": (_legacy_component(registry, "evaluator", "legacy_task_type",
                                            plan.task_spec.task_type), {}),
        }
        agents = {
            name: (_legacy_component(registry, "agent", "legacy_alias", name,
                                     task_type=plan.task_spec.task_type), {})
            for name in plan.agents
        }
    else:
        if set(plan.components) != {"backend", "task", "evaluator", "agents"}:
            raise ValueError("experiment components must select backend, task, evaluator, and agents")
        selected = {kind: _selection(plan.components[kind], kind)
                    for kind in ("backend", "task", "evaluator")}
        raw_agents = plan.components["agents"]
        if not isinstance(raw_agents, dict) or set(raw_agents) != set(plan.agents):
            raise ValueError("components.agents must select every experiment agent")
        agents = {name: _selection(value, "agent") for name, value in raw_agents.items()}
    choices = tuple(selected.values()) + tuple(agents.values())
    report = registry.preflight({identifier: config for identifier, config in choices})
    if not report.ok:
        raise PluginRegistryError(report.issues)
    for kind, (identifier, config) in selected.items():
        registry.resolve(identifier, expected_type=kind, config=config)
    for identifier, config in agents.values():
        registry.resolve(identifier, expected_type="agent", config=config)
    if registry.resolve(selected["backend"][0]).capabilities.get("requires_explicit_enable"):
        raise ValueError("this experiment CLI requires a non-interactive backend")
    recorded_components = {
        kind: {"id": identifier, "config": registry.recordable_config(identifier, config)}
        for kind, (identifier, config) in selected.items()
    }
    recorded_components["agents"] = {
        name: {"id": identifier, "config": registry.recordable_config(identifier, config)}
        for name, (identifier, config) in agents.items()
    }
    def make(kind: str, *, spec=False):
        identifier, config = selected[kind]
        kwargs = {"spec": plan.task_spec} if spec else {}
        return registry.instantiate(identifier, config, expected_type=kind, **kwargs)
    return ExperimentManager(
        output,
        backend_factory=lambda: make("backend"),
        task_factory=lambda: make("task"),
        evaluator_factory=lambda: make("evaluator", spec=True),
        agent_factories={
            name: (lambda identifier=identifier, config=config:
                   registry.instantiate(identifier, config, expected_type="agent",
                                        spec=plan.task_spec))
            for name, (identifier, config) in agents.items()
        },
        recordable_components=recorded_components,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("experiment.example.json"))
    parser.add_argument("--output", type=Path, default=Path("experiments"))
    args = parser.parse_args(argv)
    plan = ExperimentPlan.from_dict(json.loads(args.config.read_text(encoding="utf-8")))
    try:
        manager = build_manager(plan, args.output)
    except (ValueError, PluginRegistryError) as exc:
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
