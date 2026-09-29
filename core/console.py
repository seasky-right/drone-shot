"""平台工作流程的简明终端界面。"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import webbrowser
from typing import Callable

from contracts import BackendConfig, EpisodeEvent, StepRecord, TaskSpec
from .assembly import discover_plugins
from .cli import build_runner
from .plugin_cli import _preflight, run_benchmark, run_multi
from .plugins import PluginRegistry, PluginRegistryError
from .recorder import Recorder
from .replay import read_episode
from .store import EpisodeStore, StoreError
from .console_viewer import LiveViewer


def _features() -> None:
    print("已实现功能")
    print("  单次 ReachPoint   Mock / 已启动的 AirSim；Agent: fixed, direct, fixed-route, hover")
    print("  运行记录          任务、轨迹、事件、指标和传感器文件引用")
    print("  Mock 批量实验     多 Agent / seed / repeat；JSON、CSV、Markdown 汇总")
    print("  离线 ReachPoint 评价 读取标准记录，生成单次评分与同条件比较")
    print("  记录重读          校验历史 episode 和传感器文件，不重新执行飞行")
    print("  v0.2 插件选择    catalog 查看全部插件；select 从 v0.2 运行配置交互选择并执行")


_KIND_LABELS = {
    "backend": "后端", "scenario": "场景", "scenario_generator": "场景生成器",
    "task": "任务", "agent": "智能体", "runtime_provider": "运行环境",
    "evaluator": "评价器", "training_driver": "训练驱动",
    "result_processor": "结果处理器", "benchmark": "基准测试",
}

_ROLE_LABELS = {
    "backend": "后端", "scenario": "场景", "generator": "场景生成器",
    "task": "任务", "runtime": "运行环境", "evaluator": "评价器",
    "benchmark": "基准测试", "processor": "结果处理器",
}


def _role_label(role: str) -> str:
    if role.startswith("agents."):
        return f"智能体 {role.split('.', 1)[1]}"
    return _ROLE_LABELS[role]


def _backend_label(item) -> str:
    if "airsim" in item.id.lower() or item.capabilities.get("legacy_backend_type") == "airsim":
        return "真实 AirSim；运行时必须显式指定 --enable-airsim"
    if "mock" in item.id.lower():
        return "Mock（无需模拟器）"
    if item.capabilities.get("requires_explicit_enable"):
        return "运行前必须显式启用"
    return "运行前请确认后端环境"


def _catalog(registry: PluginRegistry) -> None:
    for kind, label in _KIND_LABELS.items():
        print(f"{label} ({kind})")
        items = [item for item in registry.list() if item.type == kind]
        if not items:
            print("  （未发现组件）")
        for item in items:
            api = item.capabilities.get("component_api", item.plugin_api)
            if api != "drone.plugin.api/v0.2":
                scope = "旧版 v0.1；请使用旧版 run 命令"
            elif kind == "training_driver":
                scope = "仅供 catalog 查看和一致性检查；不能执行运行记录"
            elif kind in ("benchmark", "result_processor"):
                scope = "可用于 v0.2 基准测试选择"
            else:
                scope = "可用于 v0.2 运行选择"
            if kind == "backend":
                scope += f"; {_backend_label(item)}"
            print(f"  {item.id}  [{scope}]")
        print()
    for issue in registry.issues:
        print(f"插件注册问题：{issue.code}: {issue.message}")


def _selected_config(data: dict[str, object], registry: PluginRegistry) -> dict[str, object]:
    visible = deepcopy(data)
    components = visible.get("components", {})
    if isinstance(components, dict):
        for role, choice in components.items():
            choices = choice.values() if role == "agents" and isinstance(choice, dict) else (choice,)
            for selected in choices:
                if isinstance(selected, dict) and isinstance(selected.get("id"), str):
                    try:
                        selected["config"] = registry.recordable_config(
                            selected["id"], selected.get("config", {}))
                    except PluginRegistryError:
                        selected["config"] = "<无效或不可用；请检查原始配置>"
    for role in ("benchmark", "processor"):
        selected = visible.get(role)
        if isinstance(selected, dict) and isinstance(selected.get("id"), str):
            try:
                selected["config"] = registry.recordable_config(
                    selected["id"], selected.get("config", {}))
            except PluginRegistryError:
                selected["config"] = "<无效或不可用；请检查原始配置>"
    return visible


def _role_choices(data: dict[str, object]) -> list[tuple[str, str]]:
    components = data["components"]
    roles = [("backend", "backend"), ("scenario", "scenario"),
             ("generator", "scenario_generator"), ("task", "task")]
    roles.extend((f"agents.{name}", "agent") for name in components["agents"])
    roles.extend((("runtime", "runtime_provider"), ("evaluator", "evaluator"),
                  ("benchmark", "benchmark"), ("processor", "result_processor")))
    return roles


def _set_choice(data: dict[str, object], role: str, identifier: str,
                config: dict[str, object]) -> None:
    components = data["components"]
    if role.startswith("agents."):
        components["agents"][role.split(".", 1)[1]] = {"id": identifier, "config": config}
    else:
        if role in ("benchmark", "processor"):
            data[role] = {"id": identifier, "config": config}
            return
        if role in ("scenario", "generator"):
            components.pop("generator" if role == "scenario" else "scenario", None)
        components[role] = {"id": identifier, "config": config}


_STATIC_CONFLICTS = {"incompatible_environment", "insufficient_capacity",
                     "unsupported_action", "insufficient_capability",
                     "conflicting_capability_requirement", "incompatible_dependency"}


def _candidate_conflict(data: dict[str, object], registry: PluginRegistry,
                        role: str, identifier: str, config: dict[str, object]) -> str | None:
    trial = deepcopy(data)
    _set_choice(trial, role, identifier, config)
    components = trial["components"]
    scene = components.get("scenario", components.get("generator"))
    descriptors = {item.id: item for item in registry.list()}
    backend = descriptors.get(components["backend"]["id"])
    if backend is not None:
        source = descriptors.get(scene["id"]) if scene is not None else None
        if source is not None:
            environment = source.requires.get("environment")
            if environment is not None and backend.capabilities.get("environment") != environment:
                return "后端与场景环境不匹配"
        vehicles = trial.get("vehicles", [])
        if (isinstance(vehicles, list) and "max_vehicles" in backend.capabilities
                and backend.capabilities["max_vehicles"] < len(vehicles)):
            return "后端车辆容量不足"
        required = [("action_kinds", trial.get("required_action_kinds", [])),
                    ("sensor_resources", trial.get("required_sensor_resources", {}))]
        for selected in (components.get("task"), scene, *components.get("agents", {}).values()):
            descriptor = descriptors.get(selected.get("id")) if isinstance(selected, dict) else None
            if descriptor is not None:
                required.extend(descriptor.requires.items())
        scalar_requirements: dict[str, object] = {}
        sensor_resources: dict[str, str] = {}
        for key, value in required:
            if key in ("action_kinds", "sensor_types", "time_bases", "scenario_operations"):
                if not isinstance(value, (list, tuple)):
                    continue
                missing = set(value) - set(backend.capabilities.get(key, ()))
                if missing:
                    return f"后端不支持 {key}: {', '.join(sorted(missing))}"
            elif key == "sensor_resources":
                if not isinstance(value, dict):
                    continue
                for resource, kind in value.items():
                    if resource in sensor_resources and sensor_resources[resource] != kind:
                        return f"传感器 {resource} 的类型要求冲突"
                    sensor_resources[resource] = kind
                if backend.capabilities.get("sensor_resource_confirmation") == "post_reset":
                    missing = set(value.values()) - set(backend.capabilities.get("sensor_types", ()))
                else:
                    missing = {resource for resource, kind in value.items()
                               if backend.capabilities.get("sensor_resources", {}).get(resource) != kind}
                if missing:
                    return f"后端缺少传感器: {', '.join(sorted(missing))}"
            elif key in ("coordinate_frame", "scenario_id", "environment"):
                if key in scalar_requirements and scalar_requirements[key] != value:
                    return f"组件的 {key} 要求冲突"
                scalar_requirements[key] = value
                if backend.capabilities.get(key) != value:
                    return f"后端不满足 {key} 要求"
            elif key in ("truth_access", "bounded_execution") and value is True:
                if backend.capabilities.get(key) is not True:
                    return f"后端不满足 {key} 要求"
            elif (key == "min_vehicles" and isinstance(value, int)
                  and "max_vehicles" in backend.capabilities
                  and backend.capabilities["max_vehicles"] < value):
                return "后端车辆容量不足"
    try:
        _preflight(registry, trial, enable_backend=True)
    except PluginRegistryError as exc:
        conflict = next((issue for issue in exc.issues if issue.code in _STATIC_CONFLICTS), None)
        if conflict is not None:
            return conflict.message
    except (ValueError, KeyError, TypeError):
        pass
    return None


def _current_choice(data: dict[str, object], role: str) -> dict[str, object]:
    if role.startswith("agents."):
        return data["components"]["agents"].get(role.split(".", 1)[1], {})
    if role in ("benchmark", "processor"):
        return data.get(role, {})
    return data["components"].get(role, {})


def _choice_summary(choice: dict[str, object]) -> str:
    if not isinstance(choice, dict):
        return ""
    config = choice.get("config", {})
    if not isinstance(config, dict):
        return ""
    fields = [f"{key}={value}" for key, value in config.items()
              if isinstance(value, (str, int, float, bool)) and value != "[REDACTED]"]
    return "  (" + ", ".join(fields[:3]) + (", ..." if len(fields) > 3 else "") + ")" if fields else ""


def _event_status(event: dict[str, object]) -> None:
    fields = event.get("fields", {})
    kind = event.get("kind")
    if kind == "reset":
        print("[已连接] 初始状态已获取", flush=True)
    elif kind == "action":
        print(f"[执行中] {fields.get('vehicle_id', '?')}: {fields.get('action_kind', '?')}", flush=True)
    elif kind == "execution" and not fields.get("succeeded"):
        print(f"[动作失败] {fields.get('vehicle_id', '?')}: {fields.get('error') or '未知原因'}", flush=True)
    elif kind == "component_error":
        print(f"[错误] {fields.get('stage', '?')}: {fields.get('error', '?')}", flush=True)
    elif kind == "termination":
        print(f"[结束] {fields.get('reason', '?')}", flush=True)


def _result_summary(result: dict[str, object], root: Path, *, benchmark: bool = False) -> None:
    if benchmark:
        count = result.get("case_count", 0)
        success = result.get("success_count", 0)
        print(f"结果       {'成功' if count == success else '部分失败'}  {success}/{count} 个用例")
        print(f"基准       {result.get('benchmark', '?')}")
        for case in result.get("cases", []):
            print(f"  {case.get('case_id', '?')}: {'成功' if case.get('success') else case.get('status', '失败')}"
                  f"  运行={case.get('episode_id', '?')}")
        record_root = root / "benchmarks"
    else:
        print(f"结果       {'成功' if result.get('success') else '失败'}  状态={result.get('status', '?')}")
        print(f"步数       {result.get('step_count', '?')}")
        record_root = root
        metrics = result.get("metrics")
        if isinstance(metrics, dict) and metrics:
            print("指标       " + "  ".join(f"{key}={value}" for key, value in sorted(metrics.items())))
    run_id = result.get("episode_id", "?")
    print(f"运行 ID    {run_id}")
    print(f"记录       {(record_root / str(run_id)).resolve()}")


def _readiness(data: dict[str, object], registry: PluginRegistry,
               *, enable_airsim: bool) -> str:
    if "processor" in data and "benchmark" not in data:
        return "组合待修正：结果处理器需要先选择基准测试"
    try:
        _preflight(registry, data, enable_backend=True)
        for role, kind in (("benchmark", "benchmark"), ("processor", "result_processor")):
            if role in data:
                selected = data[role]
                registry.resolve(selected["id"], kind, selected.get("config", {}))
    except PluginRegistryError as exc:
        conflict = next((issue for issue in exc.issues
                         if issue.code == "incompatible_environment"), None)
        if conflict is not None:
            details = conflict.details
            return (f"组合不兼容：后端 {details['backend']} 与场景来源 "
                    f"{details['scenario_source']} 环境不匹配")
        return "组合待修正：" + "、".join(dict.fromkeys(issue.code for issue in exc.issues))
    except (ValueError, KeyError, TypeError) as exc:
        return f"组合待修正：{exc}"
    backend_id = data["components"]["backend"]["id"]
    backend = registry.resolve(backend_id, "backend")
    benchmark_selected = "benchmark" in data
    if backend_id == "drone.v02.airsim/backend":
        gate = "已指定 --enable-airsim；" if enable_airsim else "尚需 --enable-airsim；"
        status = f"组合兼容；真实 AirSim {gate}仍需确认场景已启动（未连接验证）"
    elif backend_id == "drone.v02.mock/backend":
        status = ("基础组合预检通过" if benchmark_selected else
                  "组合完整/预检通过，可无模拟器运行")
    elif backend.capabilities.get("requires_explicit_enable") and not enable_airsim:
        status = "组合预检通过；后端仍需显式启用，运行环境未验证"
    else:
        status = "组合预检通过；后端运行环境未验证"
    if benchmark_selected:
        status += "；基准用例尚未检查，执行前仍需验证"
    return status


def _select(data: dict[str, object], registry: PluginRegistry, output: Path,
            *, enable_airsim: bool = False,
            prompt: Callable[[str], str] = input) -> int:
    if "components" not in data or "agents" not in data["components"]:
        raise ValueError("select 需要包含 components 和 agents 的 v0.2 运行配置")
    def ask(message: str) -> str | None:
        try:
            return prompt(message).strip()
        except EOFError:
            print("\n已取消选择。")
            return None
    show_viewer = False
    while True:
        print("当前 v0.2 选择：")
        visible = _selected_config(data, registry)
        components = visible["components"]
        vehicles = data.get("vehicles", [])
        print(f"  车辆: {', '.join(map(str, vehicles)) if isinstance(vehicles, list) else '配置无效'}")
        bindings = data.get("bindings", [])
        if isinstance(bindings, list):
            print("  绑定: " + ("; ".join(
                f"{row.get('agent_id', '?')} -> {', '.join(map(str, row.get('vehicle_ids', [])))}"
                for row in bindings if isinstance(row, dict)) or "未设置"))
        print(f"  最大步数: {data.get('max_steps', 1)}")
        actions = data.get("required_action_kinds", [])
        if actions:
            print(f"  动作: {', '.join(map(str, actions))}")
        for kind, label in _KIND_LABELS.items():
            if kind == "agent":
                chosen = ", ".join(f"{name}={choice['id']}{_choice_summary(choice)}"
                                   for name, choice in components["agents"].items())
            elif kind == "scenario_generator":
                chosen = components.get("generator", {}).get(
                    "id", "未使用（已选场景）" if "scenario" in components else "未选择（场景来源必选其一）")
            elif kind == "runtime_provider":
                chosen = components.get("runtime", {}).get("id", "未启用（可选）")
            elif kind == "result_processor":
                chosen = data.get("processor", {}).get("id", "未启用（可选）")
            elif kind in ("benchmark", "training_driver"):
                chosen = (data.get("benchmark", {}).get("id", "未启用（可选）")
                          if kind == "benchmark" else "仅供目录和一致性检查")
            elif kind == "scenario":
                chosen = components.get("scenario", {}).get(
                    "id", "未使用（已选场景生成器）" if "generator" in components
                    else "未选择（场景来源必选其一）")
            elif kind == "evaluator":
                chosen = components.get("evaluator", {}).get("id", "未启用（可选）")
            else:
                chosen = components.get(kind, {}).get("id", "未选择（必选）")
            if kind != "agent":
                role = {"scenario_generator": "generator", "runtime_provider": "runtime",
                        "result_processor": "processor"}.get(kind, kind)
                choice = (visible if role in ("benchmark", "processor") else components).get(role, {})
                chosen += _choice_summary(choice)
            print(f"  {label}: {chosen}")
        print(f"当前状态: {_readiness(data, registry, enable_airsim=enable_airsim)}")
        print(f"实时可视化: {'开启' if show_viewer else '关闭'}")
        print("\n输入角色编号进行选择；输入 v 切换可视化，p 仅预检，s 保存 JSON，r 执行，q 退出。")
        roles = _role_choices(data)
        for index, (role, kind) in enumerate(roles, 1):
            print(f"  {index}. {_role_label(role)} ({kind})")
        answer = ask("> ")
        if answer is None:
            return 0
        command = answer.lower()
        if command == "q":
            return 0
        if command == "v":
            show_viewer = not show_viewer
            if show_viewer and "benchmark" in data:
                print("基准测试暂不支持实时页面；单次任务运行时可用。")
            continue
        if command == "p":
            print(f"预检结果: {_readiness(data, registry, enable_airsim=enable_airsim)}")
            continue
        if command == "s":
            path = ask("保存选择结果 JSON 到：")
            if path is None:
                return 0
            destination = Path(path)
            if not str(destination) or destination.is_dir() or destination.exists():
                print("保存失败：请输入一个尚不存在的 JSON 文件路径。")
                continue
            destination.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
            print(f"完整选择已保存到 {destination.resolve()}（包含配置中的密钥）。")
            continue
        if command == "r":
            if "processor" in data and "benchmark" not in data:
                raise ValueError("结果处理器需要先选择基准测试")
            print("正在预检查选定的组件和配置……", flush=True)
            try:
                _preflight(registry, data, enable_backend=enable_airsim)
            except PluginRegistryError as exc:
                conflict = next((issue for issue in exc.issues
                                 if issue.code == "incompatible_environment"), None)
                if conflict is not None:
                    details = conflict.details
                    raise ValueError(
                        f"组件不兼容：后端 {details['backend']} 不能运行场景来源 "
                        f"{details['scenario_source']}（需要 {details['required']} 环境，"
                        f"后端提供 {details['declared']}）。请更换后端或场景来源。") from exc
                if "explicit_enable_required" in str(exc):
                    raise ValueError("真实 AirSim 运行需要 --enable-airsim；请先确认场景已启动") from exc
                raise
            for role, kind in (("benchmark", "benchmark"), ("processor", "result_processor")):
                if role in data:
                    selected = data[role]
                    registry.resolve(selected["id"], kind, selected.get("config", {}))
            print("预检查通过，开始运行选定的 v0.2 组合。", flush=True)
            if "benchmark" in data:
                if show_viewer:
                    print("基准测试暂不支持实时页面；继续在终端显示状态。")
                print("[运行中] 正在执行基准用例……", flush=True)
                result = run_benchmark(data, output, registry=registry,
                                       enable_backend=enable_airsim)
            else:
                print("[运行中] 正在执行单次任务……", flush=True)
                viewer = None
                if show_viewer:
                    try:
                        backend_id = data["components"]["backend"]["id"]
                        backend_descriptor = registry.resolve(backend_id, "backend")
                        viewer = LiveViewer(mock=backend_descriptor.capabilities.get("environment") == "mock")
                        print(f"实时页面: {viewer.url}", flush=True)
                        try:
                            if not webbrowser.open(viewer.url):
                                print("浏览器未自动打开，请使用上面的本地地址。")
                        except Exception as exc:
                            print(f"浏览器未自动打开: {exc}；请使用上面的本地地址。")
                    except Exception as exc:
                        print(f"实时页面不可用: {exc}；继续执行任务。")
                def show_snapshot(snapshot, episode_root):
                    nonlocal viewer
                    if viewer is not None:
                        try:
                            viewer.observe(snapshot, episode_root)
                        except Exception as exc:
                            print(f"实时页面更新失败: {exc}；继续执行任务。")
                            try:
                                viewer.close()
                            except Exception:
                                pass
                            viewer = None
                def show_event(event):
                    _event_status(event)
                    if viewer is not None:
                        try:
                            viewer.event(event)
                        except Exception as exc:
                            print(f"实时页面状态更新失败: {exc}；继续执行任务。")
                try:
                    run_kwargs = {"on_snapshot": show_snapshot} if viewer is not None else {}
                    result = run_multi(data, output, registry=registry,
                                       enable_backend=enable_airsim,
                                       on_event=show_event, **run_kwargs)
                    if viewer is not None:
                        viewer.finish(str(result.get("status", "?")))
                    _result_summary(result, output)
                    if viewer is not None:
                        ask("实时页面保持打开。查看完毕后按回车关闭：")
                    return int(not result.get("success"))
                except BaseException as exc:
                    if viewer is not None:
                        try:
                            viewer.finish(f"失败：{type(exc).__name__}: {exc}")
                            ask("任务失败；实时页面保持打开。查看完毕后按回车关闭：")
                        except Exception:
                            pass
                    raise
                finally:
                    if viewer is not None:
                        try:
                            viewer.close()
                        except Exception as exc:
                            print(f"实时页面关闭失败: {exc}")
            _result_summary(result, output, benchmark="benchmark" in data)
            return int(not result.get("success", result.get("success_count", 0) == result.get("case_count", 0)))
        if not command.isdigit() or not 1 <= int(command) <= len(roles):
            print("无效选择，请输入菜单中的编号或命令。")
            continue
        role, kind = roles[int(command) - 1]
        if role == "processor" and "benchmark" not in data:
            print("结果处理器需要先选择基准测试。")
            continue
        current = _current_choice(data, role)
        options = [item for item in registry.list()
                   if item.type == kind and
                   item.capabilities.get("component_api", item.plugin_api) == "drone.plugin.api/v0.2" and
                   _candidate_conflict(data, registry, role, item.id,
                                       current.get("config", {}) if current.get("id") == item.id else {}) is None]
        if not options:
            print(f"未发现可执行的 v0.2 {_KIND_LABELS[kind]}组件。")
            continue
        print(f"选择{_role_label(role)}；直接回车保留当前选择：")
        if role in ("runtime", "evaluator", "benchmark", "processor"):
            print("  0. 取消选择")
        for index, item in enumerate(options, 1):
            label = f" [{_backend_label(item)}]" if kind == "backend" else ""
            print(f"  {index}. {item.id}{label}")
        answer = ask("> ")
        if answer is None:
            return 0
        if not answer:
            continue
        if answer == "0" and role in ("runtime", "evaluator", "benchmark", "processor"):
            (data if role in ("benchmark", "processor") else data["components"]).pop(role, None)
            if role == "benchmark":
                data.pop("processor", None)
            continue
        if not answer.isdigit() or not 1 <= int(answer) <= len(options):
            print("无效选择，请输入菜单中的编号。")
            continue
        item = options[int(answer) - 1]
        print("请输入组件配置（JSON 对象）；直接回车使用默认值：")
        raw = ask("> ")
        if raw is None:
            return 0
        try:
            config = json.loads(raw) if raw else {}
            registry.resolve(item.id, expected_type=kind, config=config)
            conflict = _candidate_conflict(data, registry, role, item.id, config)
            if conflict is not None:
                print(f"选择失败：组件不兼容：{conflict}")
                continue
            _set_choice(data, role, item.id, config)
        except (json.JSONDecodeError, PluginRegistryError, ValueError) as exc:
            print(f"选择失败：{exc}")


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
    result = subcommands.add_parser("result", help="读取已保存的 v0.2 运行或基准测试结果")
    result.add_argument("run_id")
    result.add_argument("--output", type=Path, default=Path("runs"))
    result.add_argument("--benchmark", action="store_true",
                        help="从输出目录的 benchmarks 子目录读取基准测试结果")
    result.add_argument("--json", action="store_true", help="输出完整机器可读 JSON")
    catalog = subcommands.add_parser("catalog", help="列出发现的插件、类型及可执行范围")
    catalog.add_argument("--manifest", action="append", type=Path, default=[])
    select = subcommands.add_parser("select", help="交互选择并运行 v0.2 组件组合")
    select.add_argument("--config", type=Path, required=True,
                        help="包含车辆、绑定关系和动作模式的 v0.2 运行配置 JSON")
    select.add_argument("--manifest", action="append", type=Path, default=[])
    select.add_argument("--output", type=Path, default=Path("runs"))
    select.add_argument("--enable-airsim", action="store_true",
                        help="显式允许连接已启动的真实 AirSim 场景并执行飞行")
    run = subcommands.add_parser("run", help="run one ReachPoint episode with live status")
    run.add_argument("--config", type=Path, default=Path("config.example.json"))
    run.add_argument("--agent", default="fixed")
    run.add_argument("--output", type=Path, default=Path("runs"))
    run.add_argument("--enable-airsim", action="store_true",
                     help="connect to an already running AirSim scene and command the vehicle")
    args = parser.parse_args(argv)

    if args.command == "result":
        try:
            root = args.output / "benchmarks" if args.benchmark else args.output
            saved = EpisodeStore.read_result(root, args.run_id)
            if args.json:
                print(json.dumps(saved, ensure_ascii=False, indent=2))
            else:
                _result_summary(saved, args.output, benchmark=args.benchmark)
            return 0
        except (OSError, ValueError, KeyError, StoreError) as exc:
            parser.error(f"无法读取 v0.2 结果：{exc}")

    if args.command in ("catalog", "select"):
        registry = (PluginRegistry.discover(tuple(args.manifest))
                    if args.manifest else discover_plugins())
        if args.command == "catalog":
            _catalog(registry)
            return int(bool(registry.issues))
        if not sys.stdin.isatty():
            parser.error("select 需要交互式终端；脚本可使用 drone-plugins preflight/run-multi")
        try:
            data = json.loads(args.config.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("v0.2 运行配置必须是 JSON 对象")
            return _select(data, registry, args.output, enable_airsim=args.enable_airsim)
        except (OSError, ValueError, KeyError, TypeError, PluginRegistryError) as exc:
            parser.error(f"v0.2 选择无效：{exc}")

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
