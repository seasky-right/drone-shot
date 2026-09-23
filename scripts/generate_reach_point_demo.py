"""Generate synthetic episode records to demonstrate the offline evaluator.

These trajectories are fixtures, not flights or autonomous agent algorithms.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluators.record import RECORD_SCHEMA, write_json


def observation(sequence: int, north_m: float) -> dict:
    return {
        "schema": "drone.platform.contract/v0.1", "sequence": sequence,
        "vehicle_id": "drone-1",
        "position_ned": {"north_m": north_m, "east_m": 0, "down_m": 0},
        "velocity_ned_mps": [0, 0, 0], "wall_time_ns": sequence + 1,
    }


def generate(output_dir: Path) -> list[Path]:
    fixture = json.loads((Path(__file__).resolve().parents[1] /
                          "fixtures/reach_point/c1_cases.json").read_text(encoding="utf-8"))
    generated = []
    for agent, times in (("synthetic_fast", (4, 5)), ("synthetic_slow", (8, 9))):
        for repeat, elapsed in enumerate(times):
            spec = json.loads(json.dumps(fixture["base_task"]))
            spec["task_id"] = f"reach-demo-{repeat}"
            spec["seed"] = repeat
            before, after = observation(0, 0), observation(1, 10)
            record = {
                "schema": RECORD_SCHEMA, "benchmark_id": "reach_point_local_demo_v0_1",
                "case_id": "reach-demo", "agent_id": agent,
                "episode_id": f"{agent}-{repeat}", "repeat_index": repeat,
                "task_spec": spec, "initial_observation": before,
                "steps": [{
                    "sequence": 0, "observation_before": before,
                    "action": {"schema": "drone.platform.contract/v0.1", "action_id": "a-0",
                               "kind": "move_to", "vehicle_id": "drone-1", "deadline_s": 10,
                               "target_position_ned": spec["parameters"]["target_position_ned"]},
                    "execution": {"action_id": "a-0", "accepted": True, "completed": True,
                                  "succeeded": True, "wall_time_ns": 2, "error": None},
                    "observation_after": after, "elapsed_monotonic_s": elapsed,
                }],
                "progress": {"done": True, "success": True, "reason": "success"},
                "termination_reason": "success", "events": [],
                "cleanup": {"attempted": True, "succeeded": True, "error": None},
                "environment": {"backend": "synthetic_fixture", "scene_version": "c1_demo"},
                "error": None,
            }
            path = output_dir / agent / f"record-{repeat}.json"
            write_json(path, record)
            generated.append(path)
    return generated


def main() -> None:
    parser = argparse.ArgumentParser(description="generate four synthetic ReachPoint records")
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    paths = generate(args.output_dir)
    print(f"Wrote {len(paths)} synthetic episode records to {args.output_dir}")


if __name__ == "__main__":
    main()
