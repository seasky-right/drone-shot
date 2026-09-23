"""Validate the versioned ReachPoint benchmark pack without a simulator."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts import ContractValidationError, TaskSpec
from evaluators.reach_point import EVALUATOR_VERSION
from evaluators.record import SCORING_POLICY_VERSION
from tasks.reach_point import ReachPointRules

DEFAULT_PACK = ROOT / "benchmarks/reach_point_v0_1"


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractValidationError(f"{path} must contain an object")
    return value


def validate_pack(root: Path = DEFAULT_PACK) -> dict[str, object]:
    config = _read(root / "benchmark.json")
    if config.get("schema") != "drone.benchmark.pack/v0.1":
        raise ContractValidationError("unsupported benchmark schema")
    if config.get("benchmark_id") != root.name or config.get("task_type") != "reach_point":
        raise ContractValidationError("benchmark identity or task type does not match pack")
    if (config.get("evaluator_version") != EVALUATOR_VERSION
            or config.get("scoring_policy_version") != SCORING_POLICY_VERSION):
        raise ContractValidationError("pack versions do not match the installed evaluator")
    repeats = config.get("repeats_per_agent")
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats <= 0:
        raise ContractValidationError("repeats_per_agent must be positive")
    limits = config.get("limits")
    if not isinstance(limits, dict) or any(
            isinstance(limits.get(k), bool) or not isinstance(limits.get(k), (int, float))
            or limits[k] <= 0 for k in ("max_steps", "action_frequency_hz", "wall_time_budget_s")):
        raise ContractValidationError("benchmark limits must be positive numbers")

    case_files = config.get("case_files")
    if not isinstance(case_files, list) or not case_files or len(set(case_files)) != len(case_files):
        raise ContractValidationError("case_files must be a non-empty unique array")
    cases = []
    for relative in case_files:
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ContractValidationError("case path must remain inside the pack")
        spec = TaskSpec.from_dict(_read(root / relative))
        ReachPointRules.from_spec(spec)
        cases.append(spec)

    seeds_doc = _read(root / config["seeds_file"])
    seeds = seeds_doc.get("seeds")
    if (seeds_doc.get("schema") != "drone.benchmark.seeds/v0.1"
            or not isinstance(seeds, list) or len(seeds) < repeats
            or len(set(seeds)) != len(seeds)
            or any(isinstance(v, bool) or not isinstance(v, int) for v in seeds)):
        raise ContractValidationError("seed list must contain enough unique integers")

    policy = _read(root / config["scoring_policy"])
    if (policy.get("schema") != "drone.benchmark.scoring/v0.1"
            or policy.get("version") != config.get("scoring_policy_version")
            or policy.get("goal_points") != 70 or policy.get("time_points") != 30):
        raise ContractValidationError("scoring policy is incompatible with evaluator v0.1")

    manifest = _read(root / config["baseline_manifest"])
    agents = manifest.get("agents")
    if manifest.get("schema") != "drone.benchmark.baselines/v0.1" or not isinstance(agents, list):
        raise ContractValidationError("invalid baseline manifest")
    identifiers = set()
    for item in agents:
        if not isinstance(item, dict) or set(item) != {"agent_id", "class", "parameters"}:
            raise ContractValidationError("invalid baseline entry")
        if item["agent_id"] in identifiers:
            raise ContractValidationError("baseline agent IDs must be unique")
        identifiers.add(item["agent_id"])
        module_name, separator, class_name = item["class"].partition(":")
        if not separator:
            raise ContractValidationError("baseline class must use module:Class")
        agent_class = getattr(importlib.import_module(module_name), class_name, None)
        if not isinstance(agent_class, type):
            raise ContractValidationError("baseline class is not importable")
        try:
            agent_class(**item["parameters"])
        except (TypeError, ValueError) as exc:
            raise ContractValidationError(f"invalid parameters for {item['agent_id']}") from exc
    if identifiers != {"random", "fixed_route", "rule_based"}:
        raise ContractValidationError("v0.1 requires random, fixed_route and rule_based baselines")
    return {"benchmark_id": config["benchmark_id"], "cases": len(cases),
            "seeds": len(seeds), "agents": len(agents),
            "planned_runs": len(cases) * repeats * len(agents)}


def main() -> None:
    result = validate_pack()
    print("Valid ReachPoint pack: " + ", ".join(f"{key}={value}" for key, value in result.items()))


if __name__ == "__main__":
    main()
