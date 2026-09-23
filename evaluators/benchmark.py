"""Compare complete, same-condition ReachPoint result sets."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

from contracts import ContractValidationError, TaskSpec, TerminationReason
from evaluators.record import RESULT_SCHEMA

INFRASTRUCTURE_REASONS = {
    "backend_error", "evaluator_error", "initialization_error", "task_error",
}


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractValidationError(f"{name} must be a finite number")
    return float(value)


def _ratio(part: int, total: int) -> float | None:
    return part / total if total else None


def _mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def summarize_results(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not results:
        raise ContractValidationError("at least one result is required")
    benchmark_id = results[0].get("benchmark_id")
    evaluator_version = results[0].get("evaluator_version")
    policy_version = results[0].get("scoring_policy_version")
    if not all(isinstance(v, str) and v for v in
               (benchmark_id, evaluator_version, policy_version)):
        raise ContractValidationError("benchmark and version identifiers are required")
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    conditions: dict[tuple[str, int], tuple[object, object]] = {}
    seen: set[tuple[str, str, int]] = set()
    for item in results:
        if item.get("schema") != RESULT_SCHEMA:
            raise ContractValidationError("unsupported result schema")
        if (item.get("benchmark_id"), item.get("evaluator_version"),
                item.get("scoring_policy_version")) != (benchmark_id, evaluator_version, policy_version):
            raise ContractValidationError("results have incompatible benchmark or scoring versions")
        agent, case, repeat = item.get("agent_id"), item.get("case_id"), item.get("repeat_index")
        if (not isinstance(agent, str) or not agent or not isinstance(case, str) or not case
                or isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 0):
            raise ContractValidationError("result requires agent_id, case_id and repeat_index")
        identity = (agent, case, repeat)
        if identity in seen:
            raise ContractValidationError(f"duplicate result: {identity}")
        seen.add(identity)
        key = (case, repeat)
        condition = (TaskSpec.from_dict(item.get("task_spec")).to_dict(), item.get("environment"))
        if not isinstance(item.get("environment"), dict):
            raise ContractValidationError("result environment must be an object")
        if key in conditions and conditions[key] != condition:
            raise ContractValidationError(f"different task or environment for {key}")
        conditions[key] = condition
        scores, metrics, safety = item.get("scores"), item.get("metrics"), item.get("safety")
        if not all(isinstance(v, dict) for v in (scores, metrics, safety)):
            raise ContractValidationError("result scores, metrics and safety are required")
        score = _number(scores.get("total"), "total score")
        if not 0 <= score <= 100:
            raise ContractValidationError("total score must be in [0, 100]")
        try:
            reason = TerminationReason(item.get("termination_reason"))
        except (TypeError, ValueError) as exc:
            raise ContractValidationError("invalid result termination reason") from exc
        if item.get("success") is not (reason is TerminationReason.SUCCESS):
            raise ContractValidationError("result success conflicts with termination reason")
        if not isinstance(metrics.get("goal_reached"), bool):
            raise ContractValidationError("goal_reached must be boolean")
        for name in ("path_length_m", "completion_time_s", "collision_count"):
            value = metrics.get(name)
            if value is not None:
                checked = _number(value, name)
                if checked < 0:
                    raise ContractValidationError(f"{name} must be non-negative")
        collision = metrics.get("collision_count")
        if collision is not None and (not isinstance(collision, int) or isinstance(collision, bool)):
            raise ContractValidationError("collision_count must be an integer or null")
        if safety.get("status") not in ("passed", "failed", "unavailable") or not isinstance(safety.get("rank_eligible"), bool):
            raise ContractValidationError("invalid safety status")
        grouped[agent].append(item)
    expected = set(conditions)
    for agent, episodes in grouped.items():
        if {(r["case_id"], r["repeat_index"]) for r in episodes} != expected:
            raise ContractValidationError(f"agent {agent} is missing benchmark cases/repeats")

    agents: dict[str, Any] = {}
    ranking_ready = True
    for agent, episodes in sorted(grouped.items()):
        valid = [r for r in episodes if r["termination_reason"] not in INFRASTRUCTURE_REASONS]
        infra = len(episodes) - len(valid)
        reasons = Counter(r["termination_reason"] for r in episodes)
        scores = [float(r["scores"]["total"]) for r in valid]
        completion = [float(r["metrics"]["completion_time_s"]) for r in valid
                      if r["termination_reason"] == "success"
                      and r["metrics"]["completion_time_s"] is not None]
        paths = [float(r["metrics"]["path_length_m"]) for r in valid
                 if r["metrics"]["path_length_m"] is not None]
        collision_known = [r for r in valid if r["metrics"]["collision_count"] is not None]
        disqualified = sum(1 for r in valid if r["safety"]["status"] != "passed"
                           and not r["safety"]["rank_eligible"])
        if infra or disqualified:
            ranking_ready = False
        agents[agent] = {
            "attempts": len(episodes), "evaluated_episodes": len(valid),
            "infrastructure_errors": infra, "disqualified_or_unverifiable": disqualified,
            "success_rate": _ratio(sum(
                r["termination_reason"] == "success"
                and r["metrics"]["goal_reached"] is True
                and r["metrics"]["completion_time_s"] is not None
                and r["metrics"]["completion_time_s"] <= r["task_spec"]["time_budget_s"]
                for r in valid), len(valid)),
            "score_mean": _mean(scores),
            "score_std": statistics.pstdev(scores) if scores else None,
            "median_completion_time_s": _median(completion),
            "median_path_length_m": _median(paths),
            "collision_rate": _ratio(sum(r["metrics"]["collision_count"] > 0 for r in collision_known),
                                     len(collision_known)),
            "collision_telemetry_known": len(collision_known),
            "timeout_rate": _ratio(reasons["timeout"], len(valid)),
            "error_rates_by_attempt": {reason: count / len(episodes)
                                       for reason, count in sorted(reasons.items())
                                       if reason.endswith("_error")},
            "termination_counts": dict(sorted(reasons.items())),
        }
    ranking = (sorted(agents, key=lambda a: (-agents[a]["score_mean"], a))
               if ranking_ready and all(agents[a]["score_mean"] is not None for a in agents)
               else [])
    return {
        "schema": "drone.evaluation.summary/v0.1",
        "benchmark_id": benchmark_id,
        "evaluator_version": evaluator_version,
        "scoring_policy_version": policy_version,
        "case_repeat_count": len(expected),
        "total_attempts": len(results),
        "ranking_status": "ready" if ranking else "withheld_due_to_errors_or_safety",
        "ranking": ranking,
        "agents": agents,
    }


def markdown_report(summary: Mapping[str, Any]) -> str:
    def fmt(value: object, percent: bool = False) -> str:
        if value is None:
            return "—"
        return f"{value * 100:.1f}%" if percent else f"{value:.2f}"

    lines = [f"# Benchmark comparison: {summary['benchmark_id']}", "",
             f"Evaluator: `{summary['evaluator_version']}`; scoring policy: `{summary['scoring_policy_version']}`.",
             f"{summary['case_repeat_count']} case/repeat pairs; {summary['total_attempts']} total attempts.",
             f"Ranking status: `{summary['ranking_status']}`.", "",
             "| Agent | Attempts | Evaluated | Success | Mean score | Std score | Median time (s) | Median path (m) | Collision | Timeout | Infrastructure errors |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for agent, data in summary["agents"].items():
        lines.append("| " + " | ".join([
            agent, str(data["attempts"]), str(data["evaluated_episodes"]),
            fmt(data["success_rate"], True), fmt(data["score_mean"]), fmt(data["score_std"]),
            fmt(data["median_completion_time_s"]), fmt(data["median_path_length_m"]),
            fmt(data["collision_rate"], True), fmt(data["timeout_rate"], True),
            str(data["infrastructure_errors"]),
        ]) + " |")
    lines += ["", "Success, score and timeout use evaluated episodes as denominator. Infrastructure errors remain in attempts and are reported separately.",
              "Collision rate uses only episodes with available collision telemetry. Missing telemetry or disqualifying safety failures withhold ranking.", ""]
    if summary["ranking"]:
        lines.append("Ranking: " + ", ".join(f"{i}. {agent}" for i, agent in enumerate(summary["ranking"], 1)))
        lines.append("")
    for agent, data in summary["agents"].items():
        lines.append(f"- {agent}: terminations {data['termination_counts']}; collision telemetry known in {data['collision_telemetry_known']}/{data['evaluated_episodes']} evaluated episodes.")
    return "\n".join(lines) + "\n"
