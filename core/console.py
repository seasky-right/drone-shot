"""Compact terminal view for implemented platform workflows."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from contracts import BackendConfig, EpisodeEvent, StepRecord, TaskSpec
from .cli import build_runner
from .recorder import Recorder
from .replay import read_episode


def _features() -> None:
    print("已实现功能")
    print("  单次 ReachPoint   Mock / 已启动的 AirSim；Agent: fixed, direct, fixed-route, hover")
    print("  运行记录          任务、轨迹、事件、指标和传感器文件引用")
    print("  Mock 批量实验     多 Agent / seed / repeat；JSON、CSV、Markdown 汇总")
    print("  离线 ReachPoint 评价 读取标准记录，生成单次评分与同条件比较")
    print("  记录重读          校验历史 episode 和传感器文件，不重新执行飞行")


def _show_episode(directory: Path) -> None:
    episode = read_episode(directory)
    result = episode.result
    print(f"任务       {episode.task.task_id}")
    print(f"运行       {result.episode_id}  [{episode.backend.backend_type}]")
    print(f"结果       {'成功' if result.success else '失败'} ({result.termination_reason.value})")
    print(f"收尾       {'成功' if result.cleanup.succeeded else '失败'}")
    print(f"动作/事件  {len(episode.steps)} / {len(episode.events)}")
    if result.metrics:
        metrics = "  ".join(f"{key}={value:.3f}" for key, value in sorted(result.metrics.items()))
        print(f"指标       {metrics}")
    print(f"记录       {directory.resolve()}")


def _recent_runs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    candidates = {file.parent for name in ("result.json", "INCOMPLETE") for file in root.rglob(name)}
    return sorted(candidates, key=lambda path: max(
        item.stat().st_mtime for item in (
            path / "result.json", path / "INCOMPLETE", path / "trajectory.jsonl", path / "events.jsonl"
        ) if item.is_file()
    ), reverse=True)


class ConsoleRecorder(Recorder):
    def start(self, episode_id: str, task: TaskSpec, backend: BackendConfig,
              metadata: dict[str, object] | None = None) -> Path:
        directory = super().start(episode_id, task, backend, metadata)
        agent = metadata.get("agent_type", "?") if metadata else "?"
        print(f"[运行中] {task.task_id}  backend={backend.backend_type}  agent={agent}", flush=True)
        print("[连接中] 等待 Backend reset", flush=True)
        return directory

    def step(self, step: StepRecord) -> None:
        super().step(step)
        position = step.observation_after.position_ned
        print(f"[步骤 {step.sequence + 1}] {step.action.kind.value}  "
              f"NED=({position.north_m:.2f}, {position.east_m:.2f}, {position.down_m:.2f})", flush=True)

    def event(self, event: EpisodeEvent) -> None:
        super().event(event)
        fields = event.fields
        if event.kind == "reset":
            print("[已连接] 初始状态已获取", flush=True)
        elif event.kind == "action":
            print(f"[执行中] {fields['action_kind']}  {fields['action_id']}", flush=True)
        elif event.kind == "execution":
            print(f"[动作结果] {'成功' if fields['succeeded'] else '失败'}", flush=True)
        elif event.kind == "component_error":
            error = fields["error"]
            print(f"[错误] {fields['stage']}: {error['message']}", flush=True)
        elif event.kind == "cleanup":
            print(f"[收尾] {'成功' if fields['succeeded'] else '失败'}", flush=True)
        elif event.kind == "cleanup_error":
            print(f"[收尾] 失败: {fields['error']['message']}", flush=True)


def main(argv: list[str] | None = None) -> int:
    if not sys.stdout.isatty() and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("features", help="show implemented functions")
    status = subcommands.add_parser("status", help="show recent episode results")
    status.add_argument("--output", type=Path, default=Path("runs"))
    show = subcommands.add_parser("show", help="show one recorded episode, or the latest")
    show.add_argument("episode", nargs="?", type=Path)
    show.add_argument("--output", type=Path, default=Path("runs"))
    run = subcommands.add_parser("run", help="run one ReachPoint episode with live status")
    run.add_argument("--config", type=Path, default=Path("config.example.json"))
    run.add_argument("--agent", default="fixed")
    run.add_argument("--output", type=Path, default=Path("runs"))
    run.add_argument("--enable-airsim", action="store_true",
                     help="connect to an already running AirSim scene and command the vehicle")
    args = parser.parse_args(argv)

    if args.command in (None, "features"):
        _features()
        if args.command == "features":
            return 0
        print()
        args.output = Path("runs")
        args.command = "status"
    if args.command == "status":
        print("最近运行")
        recent = _recent_runs(args.output)
        if not recent:
            print("  暂无记录")
        for directory in recent[:5]:
            if (directory / "INCOMPLETE").exists():
                print(f"  {directory.name}  进行中或未完成")
                continue
            try:
                episode = read_episode(directory)
                result = episode.result
                print(f"  {directory.name}  [{episode.backend.backend_type}] {episode.task.task_id}  "
                      f"{'成功' if result.success else result.termination_reason.value}  "
                      f"收尾={'成功' if result.cleanup.succeeded else '失败'}")
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                print(f"  {directory.name}  记录不可读: {exc}")
        return 0
    if args.command == "show":
        recent = _recent_runs(args.output) if args.episode is None else []
        directory = args.episode or next((path for path in recent if not (path / "INCOMPLETE").exists()), None)
        if directory is None:
            parser.error(f"no complete episodes in {args.output}")
        try:
            _show_episode(directory)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            parser.error(f"cannot read episode: {exc}")
        return 0

    try:
        data = json.loads(args.config.read_text(encoding="utf-8"))
        spec = TaskSpec.from_dict(data["task_spec"])
        backend_config = BackendConfig.from_dict(data["backend_config"])
        recorder = ConsoleRecorder(args.output)
        runner = build_runner(spec, backend_config, args.agent, recorder,
                              enable_airsim=args.enable_airsim,
                              components=data.get("components"))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.error(f"invalid run configuration: {exc}")
    result = runner.run(spec, backend_config)
    print()
    if (recorder.directory / "INCOMPLETE").exists():
        print(f"[记录未完成] {recorder.directory.resolve()}")
    else:
        _show_episode(recorder.directory)
    return 0 if result.success and result.cleanup.succeeded is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
