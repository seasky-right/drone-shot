"""Terminal view over existing episode execution and recordings."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from core.console import _candidate_conflict, _select, main
from core.plugins import PluginRegistry
from core.replay import read_episode


ROOT = Path(__file__).resolve().parents[1]


class ConsoleTests(unittest.TestCase):
    def v02_registry(self):
        return PluginRegistry.discover(manifest_paths=(ROOT / "builtin_pack" / "drone_plugin.json",))

    def test_catalog_shows_all_v02_kinds_and_execution_scope(self) -> None:
        with redirect_stdout(printed := StringIO()):
            self.assertEqual(main(["catalog", "--manifest",
                                   str(ROOT / "builtin_pack" / "drone_plugin.json")]), 0)
        output = printed.getvalue()
        for kind in ("backend", "scenario", "scenario_generator", "task", "agent",
                     "runtime_provider", "evaluator", "training_driver",
                     "result_processor", "benchmark"):
            self.assertIn(f"({kind})", output)
        self.assertIn("后端 (backend)", output)
        self.assertIn("drone.v02.mock/backend  [可用于 v0.2 运行选择; Mock（无需模拟器）]", output)
        self.assertIn("drone.v02.airsim/backend  [可用于 v0.2 运行选择; 真实 AirSim", output)
        self.assertIn("--enable-airsim", output)
        self.assertIn("仅供 catalog 查看和一致性检查", output)
        self.assertIn("旧版 v0.1", output)

    def test_select_displays_summary_and_unselected_kinds(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-v02.json").read_text(encoding="utf-8"))
        with TemporaryDirectory() as directory, redirect_stdout(printed := StringIO()):
            self.assertEqual(_select(data, self.v02_registry(), Path(directory),
                                     prompt=lambda _: "q"), 0)
        output = printed.getvalue()
        self.assertIn("车辆: A, B", output)
        self.assertIn("绑定: central -> A, B", output)
        self.assertIn("target_north_m=5", output)
        self.assertNotIn('"vehicles": [', output)
        self.assertIn("后端: drone.v02.mock/backend", output)
        self.assertIn("场景生成器: 未使用（已选场景）", output)
        self.assertIn("基准测试: 未启用（可选）", output)
        self.assertIn("结果处理器: 未启用（可选）", output)
        self.assertIn("当前状态: 组合完整/预检通过，可无模拟器运行", output)
        self.assertIn("训练驱动: 仅供目录和一致性检查", output)
        self.assertIn("1. 后端 (backend)", output)
        self.assertIn("输入 v 切换可视化，p 仅预检，s 保存 JSON，r 执行，q 退出", output)

    def test_select_generator_marks_scenario_as_alternative(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-v02.json").read_text(encoding="utf-8"))
        data["components"].pop("scenario")
        data["components"]["generator"] = {"id": "drone.v02.mock/generator"}
        with TemporaryDirectory() as directory, redirect_stdout(printed := StringIO()):
            self.assertEqual(_select(data, self.v02_registry(), Path(directory),
                                     prompt=lambda _: "q"), 0)
        self.assertIn("场景: 未使用（已选场景生成器）", printed.getvalue())
        self.assertIn("场景生成器: drone.v02.mock/generator", printed.getvalue())
        self.assertIn("组合完整/预检通过，可无模拟器运行", printed.getvalue())

    def test_select_airsim_fixed_scenario_shows_gate_without_connecting(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-airsim-v02.json").read_text(encoding="utf-8"))
        original = json.dumps(data, sort_keys=True)
        with TemporaryDirectory() as directory, redirect_stdout(printed := StringIO()):
            with patch("core.console.run_multi") as run:
                answers = iter(("p", "q"))
                self.assertEqual(_select(data, self.v02_registry(), Path(directory),
                                         prompt=lambda _: next(answers)), 0)
                run.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(json.dumps(data, sort_keys=True), original)
        self.assertIn("场景: drone.v02.airsim/scenario", printed.getvalue())
        self.assertIn("场景生成器: 未使用（已选场景）", printed.getvalue())
        self.assertIn("组合兼容；真实 AirSim 尚需 --enable-airsim；仍需确认场景已启动", printed.getvalue())
        self.assertIn("预检结果:", printed.getvalue())

    def test_select_airsim_benchmark_keeps_gate_and_case_caveat(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-airsim-v02.json").read_text(encoding="utf-8"))
        data["benchmark"] = {"id": "drone.v02.mock/benchmark"}
        with TemporaryDirectory() as directory, redirect_stdout(printed := StringIO()):
            self.assertEqual(_select(data, self.v02_registry(), Path(directory),
                                     prompt=lambda _: "q"), 0)
        self.assertIn("真实 AirSim 尚需 --enable-airsim；仍需确认场景已启动", printed.getvalue())
        self.assertIn("基准用例尚未检查，执行前仍需验证", printed.getvalue())

    def test_select_preflight_only_does_not_run_or_write_output(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-v02.json").read_text(encoding="utf-8"))
        with TemporaryDirectory() as directory, redirect_stdout(printed := StringIO()):
            with patch("core.console.run_multi") as run:
                answers = iter(("p", "q"))
                self.assertEqual(_select(data, self.v02_registry(), Path(directory),
                                         prompt=lambda _: next(answers)), 0)
                run.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertIn("预检结果: 组合完整/预检通过，可无模拟器运行", printed.getvalue())

    def test_select_mismatch_visible_before_run(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-v02.json").read_text(encoding="utf-8"))
        data["components"]["backend"] = {"id": "drone.v02.airsim/backend"}
        with TemporaryDirectory() as directory, redirect_stdout(printed := StringIO()):
            self.assertEqual(_select(data, self.v02_registry(), Path(directory),
                                     prompt=lambda _: "q"), 0)
        self.assertIn("组合不兼容：后端 drone.v02.airsim/backend 与场景来源 "
                      "drone.v02.mock/scenario 环境不匹配", printed.getvalue())

    def test_select_saves_full_runnable_json_without_running(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-v02.json").read_text(encoding="utf-8"))
        with TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            destination = Path(directory) / "selected.json"
            answers = iter(("s", str(destination), "q"))
            self.assertEqual(_select(data, self.v02_registry(), Path(directory) / "runs",
                                     prompt=lambda _: next(answers)), 0)
            self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), data)
            self.assertFalse((Path(directory) / "runs").exists())

    def test_processor_without_benchmark_is_rejected_before_execution(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-v02.json").read_text(encoding="utf-8"))
        data["processor"] = {"id": "drone.v02.mock/processor"}
        with TemporaryDirectory() as directory, redirect_stdout(printed := StringIO()):
            with self.assertRaisesRegex(ValueError, "结果处理器需要先选择基准测试"):
                _select(data, self.v02_registry(), Path(directory), prompt=lambda _: "r")
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertNotIn("预检查通过", printed.getvalue())

    def test_select_hides_backend_conflicting_with_scenario(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-v02.json").read_text(encoding="utf-8"))
        registry = self.v02_registry()
        answers = iter(("1", "", "q"))
        with TemporaryDirectory() as directory, redirect_stdout(printed := StringIO()):
            self.assertEqual(_select(data, registry, Path(directory),
                                     prompt=lambda _: next(answers)), 0)
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertNotIn("drone.v02.airsim/backend", printed.getvalue())
        self.assertEqual(data["components"]["backend"]["id"], "drone.v02.mock/backend")

    def test_select_incompatible_loaded_config_offers_repair(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-v02.json").read_text(encoding="utf-8"))
        data["components"]["backend"] = {"id": "drone.v02.airsim/backend"}
        answers = iter(("1", "1", "", "q"))
        with TemporaryDirectory() as directory, redirect_stdout(printed := StringIO()):
            self.assertEqual(_select(data, self.v02_registry(), Path(directory),
                                     prompt=lambda _: next(answers)), 0)
        self.assertIn("组合不兼容", printed.getvalue())
        self.assertIn("场景: drone.v02.mock/scenario", printed.getvalue())
        self.assertEqual(data["components"]["backend"]["id"], "drone.v02.mock/backend")
        self.assertIn("组合完整/预检通过", printed.getvalue())

    def test_select_hides_task_conflict_even_when_other_config_invalid(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-v02.json").read_text(encoding="utf-8"))
        data["components"]["scenario"]["config"] = {"seed": "invalid"}
        answers = iter(("4", "", "q"))
        with TemporaryDirectory() as directory, redirect_stdout(printed := StringIO()):
            self.assertEqual(_select(data, self.v02_registry(), Path(directory),
                                     prompt=lambda _: next(answers)), 0)
        self.assertNotIn("drone.v02.airsim/task.reach-point", printed.getvalue())
        self.assertIn("drone.v02.mock/task", printed.getvalue())

    def test_select_processor_requires_benchmark_in_menu(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-v02.json").read_text(encoding="utf-8"))
        answers = iter(("9", "q"))
        with TemporaryDirectory() as directory, redirect_stdout(printed := StringIO()):
            self.assertEqual(_select(data, self.v02_registry(), Path(directory),
                                     prompt=lambda _: next(answers)), 0)
        self.assertIn("结果处理器需要先选择基准测试", printed.getvalue())
        self.assertNotIn("processor", data)

    def test_undeclared_backend_capacity_does_not_hide_candidate(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-v02.json").read_text(encoding="utf-8"))
        registry = self.v02_registry()
        descriptors = tuple(replace(item, capabilities={
            key: value for key, value in item.capabilities.items() if key != "max_vehicles"})
            if item.id == "drone.v02.mock/backend" else item for item in registry.list())
        with patch.object(registry, "list", return_value=descriptors):
            self.assertIsNone(_candidate_conflict(data, registry, "backend",
                                                  "drone.v02.mock/backend", {}))

    def test_select_mock_backend_rejects_airsim_scenario_before_run(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-v02.json").read_text(encoding="utf-8"))
        data["components"]["scenario"] = {"id": "drone.v02.airsim/scenario", "config": {
            "target": {"north_m": 4, "east_m": 0.5, "down_m": -2}}}
        with TemporaryDirectory() as directory, redirect_stdout(printed := StringIO()):
            with self.assertRaisesRegex(ValueError, "组件不兼容.*drone.v02.mock/backend.*drone.v02.airsim/scenario"):
                _select(data, self.v02_registry(), Path(directory), prompt=lambda _: "r")
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertNotIn("预检查通过", printed.getvalue())

    def test_select_runs_mock_benchmark_with_processor(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-benchmark-v02.json").read_text(encoding="utf-8"))
        with TemporaryDirectory() as directory, redirect_stdout(printed := StringIO()):
            self.assertEqual(_select(data, self.v02_registry(), Path(directory),
                                     prompt=lambda _: "r"), 0)
            self.assertIn("基准测试: drone.v02.mock/benchmark", printed.getvalue())
            self.assertIn("结果处理器: drone.v02.mock/processor", printed.getvalue())
            self.assertIn("基础组合预检通过；基准用例尚未检查", printed.getvalue())
            self.assertIn("预检查通过，开始运行", printed.getvalue())
            self.assertTrue((Path(directory) / "benchmarks" / "builtin-v02-benchmark" /
                             "result.json").is_file())
            with redirect_stdout(summary := StringIO()):
                self.assertEqual(main(["result", "builtin-v02-benchmark", "--benchmark",
                                       "--output", directory]), 0)
            self.assertIn("2/2 个用例", summary.getvalue())
            with redirect_stdout(summary := StringIO()):
                self.assertEqual(main(["result", "builtin-v02-benchmark", "--benchmark",
                                       "--json", "--output", directory]), 0)
            self.assertEqual(json.loads(summary.getvalue())["benchmark"],
                             "drone.v02.mock/benchmark")

    def test_select_run_and_result_read_mock_episode(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-v02.json").read_text(encoding="utf-8"))
        with TemporaryDirectory() as directory, redirect_stdout(printed := StringIO()):
            self.assertEqual(_select(data, self.v02_registry(), Path(directory),
                                     prompt=lambda _: "r"), 0)
            self.assertIn("[已连接]", printed.getvalue())
            self.assertIn("步数       1", printed.getvalue())
            self.assertNotIn('"final_snapshot"', printed.getvalue())
            with redirect_stdout(result := StringIO()):
                self.assertEqual(main(["result", data["episode_id"],
                                       "--output", directory]), 0)
            self.assertIn("结果       成功", result.getvalue())
            with redirect_stdout(result := StringIO()):
                self.assertEqual(main(["result", data["episode_id"],
                                       "--json", "--output", directory]), 0)
            self.assertTrue(json.loads(result.getvalue())["success"])

    def test_select_command_requires_tty(self) -> None:
        with patch("sys.stdin.isatty", return_value=False), redirect_stderr(stderr := StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main(["select", "--config", str(ROOT / "builtin_pack" / "sample-v02.json")])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("需要交互式终端", stderr.getvalue())

    def test_select_exits_cleanly_on_end_of_input(self) -> None:
        data = json.loads((ROOT / "builtin_pack" / "sample-v02.json").read_text(encoding="utf-8"))
        with TemporaryDirectory() as directory, redirect_stdout(printed := StringIO()):
            self.assertEqual(_select(data, self.v02_registry(), Path(directory),
                                     prompt=lambda _: (_ for _ in ()).throw(EOFError())), 0)
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertIn("已取消选择", printed.getvalue())

    def test_run_status_and_show_use_one_recorded_mock_episode(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "runs" / "mock-check"
            printed = StringIO()
            with redirect_stdout(printed):
                exit_code = main(["run", "--config", str(ROOT / "config.example.json"),
                                  "--output", str(output)])
            self.assertEqual(exit_code, 0)
            self.assertIn("[运行中]", printed.getvalue())
            self.assertIn("[执行中] move_to", printed.getvalue())
            self.assertIn("final_distance_m=0.000", printed.getvalue())
            episode = next(output.iterdir())
            self.assertTrue(read_episode(episode).result.success)

            with redirect_stdout(printed := StringIO()):
                self.assertEqual(main(["status", "--output", str(output.parent)]), 0)
            self.assertIn("reach-point-example", printed.getvalue())
            with redirect_stdout(printed := StringIO()):
                self.assertEqual(main(["show", "--output", str(output.parent)]), 0)
            self.assertIn("动作/事件  1 / 5", printed.getvalue())

    def test_status_marks_incomplete_record_without_claiming_it_is_running(self) -> None:
        with TemporaryDirectory() as directory:
            episode = Path(directory) / "unfinished"
            episode.mkdir()
            (episode / "INCOMPLETE").write_text("unfinished", encoding="utf-8")
            with redirect_stdout(printed := StringIO()):
                self.assertEqual(main(["status", "--output", directory]), 0)
            self.assertIn("进行中或未完成", printed.getvalue())

    def test_airsim_requires_explicit_gate_before_backend_creation(self) -> None:
        with TemporaryDirectory() as directory:
            data = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            data["backend_config"]["backend_type"] = "airsim"
            config = Path(directory) / "config.json"
            config.write_text(json.dumps(data), encoding="utf-8")
            with patch("backends.airsim.AirSimBackend") as backend, redirect_stderr(stderr := StringIO()):
                with self.assertRaises(SystemExit) as result:
                    main(["run", "--config", str(config), "--output", directory])
            self.assertEqual(result.exception.code, 2)
            self.assertIn("--enable-airsim", stderr.getvalue())
            backend.assert_not_called()


if __name__ == "__main__":
    unittest.main()
