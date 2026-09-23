"""Independent real-AirSim smoke run; no EpisodeRunner dependency."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from backends.airsim import AirSimBackend
from contracts import Action, ActionKind, BackendConfig, PositionNed, TaskSpec


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="JSON with backend_config, task_spec and waypoint_ned")
    parser.add_argument("output", type=Path, help="dedicated output directory for smoke evidence")
    parser.add_argument("--execute", action="store_true", help="required to send commands to a running simulator")
    args = parser.parse_args()
    source = json.loads(args.config.read_text(encoding="utf-8"))
    task = TaskSpec.from_dict(source["task_spec"])
    parsed = BackendConfig.from_dict(source["backend_config"])
    target = PositionNed.from_dict(source["waypoint_ned"])
    if parsed.backend_type != "airsim":
        parser.error("backend_config.backend_type must be airsim")
    if not args.execute:
        print("Configuration parsed; pass --execute to connect and fly.")
        return 0
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    backend_config = BackendConfig(parsed.backend_type, parsed.vehicle_id, parsed.connection, str(output))
    backend = AirSimBackend()
    evidence: dict[str, object] = {"task": task.to_dict(), "backend_config": backend_config.to_dict(), "waypoint_ned": target.to_dict(), "started_wall_time_ns": time.time_ns()}
    success = False
    try:
        initial = backend.reset(backend_config, task)
        evidence["initial_observation"] = initial.to_dict()
        action = Action("smoke-move", ActionKind.MOVE_TO, parsed.vehicle_id, float(source.get("move_deadline_s", 30)), target)
        movement = backend.execute(action)
        evidence["move_execution"] = movement.to_dict()
        evidence["after_move"] = backend.observe().to_dict()
        if not movement.succeeded:
            raise RuntimeError(f"move_to failed: {movement.error}")
        hover = Action("smoke-hover", ActionKind.HOVER, parsed.vehicle_id, float(source.get("hover_deadline_s", 5)))
        hovered = backend.execute(hover)
        evidence["hover_execution"] = hovered.to_dict()
        evidence["after_hover"] = backend.observe().to_dict()
        if not hovered.succeeded:
            raise RuntimeError(f"hover failed: {hovered.error}")
        success = True
    except Exception as exc:
        evidence["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        cleanup = backend.cleanup()
        evidence["cleanup"] = cleanup.to_dict()
        evidence["runtime_metadata"] = backend.runtime_metadata
        backend.close()
        evidence["finished_wall_time_ns"] = time.time_ns()
        evidence["success"] = success and cleanup.succeeded is True
        (output / "smoke_result.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output / "smoke_result.json")
    return 0 if evidence["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
