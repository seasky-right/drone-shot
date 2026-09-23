"""Task-independent batch execution and comparison over EpisodeRunner results."""
from __future__ import annotations

import csv
from dataclasses import dataclass, replace
import json
from pathlib import Path
import re
import statistics
from typing import Callable, Mapping
from uuid import uuid4

from contracts import BackendConfig, TaskSpec, TerminationReason
from .recorder import Recorder
from .runner import EpisodeRunner


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_RESERVED_WINDOWS = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_INFRASTRUCTURE_REASONS = {
    TerminationReason.BACKEND_ERROR.value,
    TerminationReason.TASK_ERROR.value,
    TerminationReason.EVALUATOR_ERROR.value,
    TerminationReason.INITIALIZATION_ERROR.value,
}


@dataclass(frozen=True)
class ExperimentPlan:
    task_spec: TaskSpec
    backend_config: BackendConfig
    agents: tuple[str, ...]
    seeds: tuple[int, ...]
    repeats: int = 1
    experiment_id: str | None = None

    def __post_init__(self) -> None:
        if not self.agents or any(not isinstance(name, str) or not name.strip() for name in self.agents):
            raise ValueError("agents must contain nonempty names")
        if len(set(self.agents)) != len(self.agents):
            raise ValueError("agent names must be unique")
        if not self.seeds or any(isinstance(seed, bool) or not isinstance(seed, int) for seed in self.seeds):
            raise ValueError("seeds must contain integers")
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("seeds must be unique")
        if isinstance(self.repeats, bool) or not isinstance(self.repeats, int) or self.repeats < 1:
            raise ValueError("repeats must be a positive integer")
        if self.experiment_id is not None:
            value = self.experiment_id
            if not _SAFE_ID.fullmatch(value) or value.endswith(".") or value.split(".")[0].lower() in _RESERVED_WINDOWS:
                raise ValueError("experiment_id must be a safe directory name")

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ExperimentPlan":
        if not isinstance(value, Mapping):
            raise ValueError("experiment plan must be an object")
        allowed = {"task_spec", "backend_config", "agents", "seeds", "repeats", "experiment_id"}
        if set(value) - allowed or not {"task_spec", "backend_config", "agents", "seeds"} <= set(value):
            raise ValueError("experiment plan has missing or unknown fields")
        agents, seeds = value["agents"], value["seeds"]
        if not isinstance(agents, list) or not isinstance(seeds, list):
            raise ValueError("agents and seeds must be arrays")
        return cls(
            TaskSpec.from_dict(value["task_spec"]),
            BackendConfig.from_dict(value["backend_config"]),
            tuple(agents), tuple(seeds),
            value.get("repeats", 1), value.get("experiment_id"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "task_spec": self.task_spec.to_dict(),
            "backend_config": self.backend_config.to_dict(),
            "agents": list(self.agents),
            "seeds": list(self.seeds),
            "repeats": self.repeats,
            "experiment_id": self.experiment_id,
        }


@dataclass(frozen=True)
class ExperimentResult:
    experiment_id: str
    directory: Path
    summary: Mapping[str, object]


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _metric_stats(rows: list[dict[str, object]]) -> dict[str, dict[str, float | int]]:
    names = sorted({name for row in rows for name in row["metrics"]})
    answer: dict[str, dict[str, float | int]] = {}
    for name in names:
        values = [float(row["metrics"][name]) for row in rows if name in row["metrics"]]
        answer[name] = {
            "count": len(values),
            "missing_count": len(rows) - len(values),
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "std": statistics.pstdev(values),
            "min": min(values),
            "max": max(values),
        }
    return answer


def _summarize(plan: ExperimentPlan, rows: list[dict[str, object]], stopped_early: bool) -> dict[str, object]:
    groups: dict[str, object] = {}
    for agent in plan.agents:
        items = [row for row in rows if row["agent"] == agent]
        counts: dict[str, int] = {}
        for item in items:
            reason = str(item["termination_reason"])
            counts[reason] = counts.get(reason, 0) + 1
        success_count = sum(bool(item["success"]) for item in items)
        groups[agent] = {
            "planned_episode_count": len(plan.seeds) * plan.repeats,
            "episode_count": len(items),
            "task_success_count": success_count,
            "task_success_rate": success_count / len(items) if items else None,
            "infrastructure_error_count": sum(bool(item["infrastructure_error"]) for item in items),
            "cleanup_failure_count": sum(item["cleanup_succeeded"] is False for item in items),
            "termination_counts": counts,
            "metrics": _metric_stats(items),
        }
    return {
        "experiment_id": plan.experiment_id,
        "planned_episode_count": len(plan.agents) * len(plan.seeds) * plan.repeats,
        "episode_count": len(rows),
        "stopped_early": stopped_early,
        "seed_semantics": "TaskSpec.seed is set for each episode; actual backend and agent randomization must be verified separately.",
        "agents": groups,
    }


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    metric_names = sorted({name for row in rows for name in row["metrics"]})
    columns = ["agent", "seed", "repeat", "episode_id", "termination_reason", "success",
               "cleanup_succeeded", "infrastructure_error", "result_path", "error"] + [f"metric.{name}" for name in metric_names]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            values = {column: row.get(column) for column in columns if not column.startswith("metric.")}
            values.update({f"metric.{name}": row["metrics"].get(name) for name in metric_names})
            writer.writerow(values)


def _write_report(path: Path, summary: Mapping[str, object]) -> None:
    lines = [
        f"# Experiment {summary['experiment_id']}",
        "",
        f"Episodes: {summary['episode_count']} / {summary['planned_episode_count']}. Stopped early: {summary['stopped_early']}.",
        "",
        "| Agent | Task successes / episodes | Task success rate | Infrastructure errors | Cleanup failures |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, item in summary["agents"].items():
        rate = "n/a" if item["task_success_rate"] is None else f"{item['task_success_rate']:.3f}"
        lines.append(f"| {name} | {item['task_success_count']} / {item['episode_count']} | {rate} | {item['infrastructure_error_count']} | {item['cleanup_failure_count']} |")
    lines.extend(["", "## Termination reasons", "", "| Agent | Reason | Episodes |", "| --- | --- | ---: |"])
    for name, item in summary["agents"].items():
        for reason, count in sorted(item["termination_counts"].items()):
            lines.append(f"| {name} | {reason} | {count} |")
    lines.extend(["", "## Numeric metrics", "", "| Agent | Metric | Available / episodes | Mean | Median | Std | Min | Max |",
                  "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"])
    for name, item in summary["agents"].items():
        for metric, stats in item["metrics"].items():
            lines.append(f"| {name} | {metric} | {stats['count']} / {item['episode_count']} | {stats['mean']:.3f} | {stats['median']:.3f} | {stats['std']:.3f} | {stats['min']:.3f} | {stats['max']:.3f} |")
    lines.extend(["", "Task failures, timeouts and infrastructure errors remain in each agent's episode denominator.",
                  "Metrics aggregate only available values; missing values are counted separately in summary.json.",
                  "Task success is reported separately from cleanup status.",
                  str(summary["seed_semantics"]),
                  "Recorded-outcome replay does not establish independent random scenes or real-simulator performance.", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


class ExperimentManager:
    """Create fresh components for each episode and preserve every attempted run."""

    def __init__(
        self,
        root: str | Path,
        *,
        backend_factory: Callable[[], object],
        task_factory: Callable[[], object],
        evaluator_factory: Callable[[], object],
        agent_factories: Mapping[str, Callable[[], object]],
    ) -> None:
        self.root = Path(root)
        self.backend_factory = backend_factory
        self.task_factory = task_factory
        self.evaluator_factory = evaluator_factory
        self.agent_factories = dict(agent_factories)

    def run(self, plan: ExperimentPlan) -> ExperimentResult:
        unknown = set(plan.agents) - self.agent_factories.keys()
        if unknown:
            raise ValueError(f"unknown agents: {', '.join(sorted(unknown))}")
        identifier = plan.experiment_id or f"exp-{uuid4().hex}"
        if plan.experiment_id is None:
            plan = replace(plan, experiment_id=identifier)
        directory = self.root / identifier
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "INCOMPLETE").write_text("experiment output is not complete\n", encoding="utf-8")
        episodes = directory / "episodes"
        episodes.mkdir()
        _write_json(directory / "config.json", plan.to_dict())
        rows: list[dict[str, object]] = []
        stopped_early = False
        index = 0
        for agent_name in plan.agents:
            if stopped_early:
                break
            for seed in plan.seeds:
                if stopped_early:
                    break
                for repeat_number in range(1, plan.repeats + 1):
                    index += 1
                    episode_id = f"episode-{index:06d}"
                    row: dict[str, object] = {
                        "agent": agent_name, "seed": seed, "repeat": repeat_number,
                        "episode_id": episode_id, "termination_reason": "infrastructure_error",
                        "success": False, "cleanup_succeeded": None,
                        "infrastructure_error": True, "result_path": None,
                        "error": None, "metrics": {},
                    }
                    try:
                        runner = EpisodeRunner(
                            self.backend_factory(), self.agent_factories[agent_name](),
                            self.task_factory(), self.evaluator_factory(), Recorder(episodes),
                        )
                        result = runner.run(replace(plan.task_spec, seed=seed), plan.backend_config, episode_id=episode_id)
                        row.update({
                            "termination_reason": result.termination_reason.value,
                            "success": result.success,
                            "cleanup_succeeded": result.cleanup.succeeded,
                            "infrastructure_error": result.termination_reason.value in _INFRASTRUCTURE_REASONS,
                            "result_path": f"episodes/{episode_id}/result.json",
                            "metrics": dict(result.metrics),
                        })
                        if result.termination_reason is TerminationReason.CANCELLED:
                            stopped_early = True
                    except Exception as exc:
                        row["error"] = f"{type(exc).__name__}: {exc}"
                    rows.append(row)
                    if stopped_early:
                        break
        summary = _summarize(plan, rows, stopped_early)
        _write_csv(directory / "metrics.csv", rows)
        _write_json(directory / "summary.json", summary)
        _write_report(directory / "report.md", summary)
        (directory / "INCOMPLETE").unlink()
        return ExperimentResult(identifier, directory, summary)
