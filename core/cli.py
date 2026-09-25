"""Run one episode from a platform config JSON and installed components."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from contracts import BackendConfig, TaskSpec
from core import EpisodeRunner, Recorder
from .assembly import build_plugin_runner
from .plugins import PluginRegistryError


def build_runner(spec: TaskSpec, backend_config: BackendConfig, agent_name: str,
                 recorder: Recorder, *, enable_airsim: bool = False,
                 components: dict[str, object] | None = None) -> EpisodeRunner:
    return build_plugin_runner(spec, backend_config, agent_name, recorder,
                               components=components, enable_airsim=enable_airsim)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.example.json"))
    parser.add_argument("--agent", default="fixed")
    parser.add_argument("--output", type=Path, default=Path("runs"))
    parser.add_argument("--enable-airsim", action="store_true",
                        help="allow this run to connect, take off, move, and land in AirSim")
    args = parser.parse_args(argv)
    data = json.loads(args.config.read_text(encoding="utf-8"))
    spec = TaskSpec.from_dict(data["task_spec"])
    backend_config = BackendConfig.from_dict(data["backend_config"])
    try:
        runner = build_runner(spec, backend_config, args.agent, Recorder(args.output),
                              enable_airsim=args.enable_airsim,
                              components=data.get("components"))
    except (ValueError, PluginRegistryError) as exc:
        parser.error(str(exc))
    result = runner.run(spec, backend_config)
    print(json.dumps(result.to_dict(), ensure_ascii=False))
    return 0 if result.success and result.cleanup.succeeded is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
