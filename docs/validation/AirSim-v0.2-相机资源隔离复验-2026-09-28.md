# AirSim v0.2 相机资源隔离复验（2026-09-28）

本次用当前 Core 与 builtin Pack 的安装版，在完整复制的 UE4 `WindowsNoEditor` 固定场景中运行一次显式要求 `rgb:0` 和 `depth:0` 的单机 episode。结果为 `success`、1 步，确认后的能力文件记录两个资源 ID；本记录只覆盖该固定场景的相机资源能力协商与单次 ReachPoint 技术流程。原始文件位于 Git 忽略的 `runs/real-airsim-v02-resource-retest-20260928/`，仅本机保留。

## 隔离与输入

- 冻结源：`F:/Project/drone/材料-无人机遥感实习-AirSIM部分/scene/WindowsNoEditor`。独立副本：`F:/Project/drone/runs/real-airsim-v02-resource-retest-20260928/scene/WindowsNoEditor`。复制前目标不存在，源树有 74 个文件、2,489,601,679 字节；F: 可用 70,165,630,976 字节。复制整个 `WindowsNoEditor` 层级，启动路径为副本中的 `ModularNeighborhood/Binaries/Win64/ModularNeighborhood.exe`，工作目录为该 EXE 的父目录。
- `inventory_scene.py` 在两个场景树之外记录每个文件的相对路径、字节数和 SHA-256。`source-before.jsonl`、`source-after-copy.jsonl`、`copy-before.jsonl` 的文件 SHA-256 都是 `988451b8b761cbd761c59807812d341702e0c23c363944df2145a97da3221b82`，逐项一致。启动前无 UE4 场景进程或 41451 监听；旧 `runs/real-airsim-20260924/STOP5` 未动。
- 以 Python 3.13.5 构建并在本次新 `venv` 中安装两个本地 wheel：Core SHA-256 `5403def046966081c1ee5b004df001182ffe1072128d25da096885478b31f5f9`，Pack SHA-256 `b37c08d5ea5637bee8e139e83997fa7558fbc5c10fc24df309d4bc6fbb225167`。`python -P` 确认两个包均从新 `venv/Lib/site-packages` 导入。`required-cameras.json` 由已安装 Pack 的样例生成，加入 `required_sensor_resources={"rgb:0":"drone/rgb","depth:0":"drone/depth"}` 和独立 episode ID。安装版 `preflight --enable-backend` 返回 `ok: true`。

## 实测与收尾

- 新 `scene_guard.py` 从副本启动场景并自动选择 Drone，确认 `127.0.0.1:41451` 就绪，RPC 就绪后最多运行 300 秒。安装版 `core.plugin_cli run-multi required-cameras.json --output episodes --enable-backend` 退出 0；episode `airsim-v02-required-cameras-isolated` 为 `success`、1 步，动作 `move-0` 成功，`cleanup_errors=[]`。
- `metadata/capabilities.json` 是 reset 前声明，`sensor_resources={}`；`metadata/capabilities-confirmed.json` 在 reset 后记录 `rgb:0` 对应 `drone/rgb`、`depth:0` 对应 `drone/depth`。两次观察的 `missing_sensors` 都为空。两张 RGB 文件分别为 77,067 和 86,200 字节，均有 PNG 签名；两份 Depth 文件均为 147,456 字节，符合 256×144 float32。`show-result` 退出 0，重读与 `result.json` 一致；`verify_retest.py` 通过，机器可读摘要为 `verification.json`。
- episode 完成后立即创建本次目录的 `STOP5`。`guard.stdout.log` 记录 `scene_stopped`、场景退出码 0；随后 guard/场景 PID 均不存在，41451 无监听。未触碰旧 STOP5。清理无报错不等于取得落地或解除武装后的独立物理读数。
- `source-after-run.jsonl` 与运行前源清单的 SHA-256 完全相同，冻结源 74 项的路径、大小及内容均未变化。副本运行后为 76 项：新增一个 `Saved/Config/CrashReportClient/<新 ID>/CrashReportClient.ini` 和一个日志备份，修改 `Saved/Logs/ModularNeighborhood.log`；未删除副本文件。只读执行 `freeze_baseline.py --verify` 仍返回 `BASELINE_MISMATCH`，本次未运行基线写入器，也未将现有副本称为原始 1,389 项基线。

## 范围

缺少相机时禁止 Agent 行动由无仿真 FakeRpc 用例 `test_unknown_resource_fails_after_reset_before_agent_action` 及 `test_unconfirmed_resource_fails_before_agent_action_and_cleans_up` 覆盖；修复侧报告完整测试 319 通过、2 跳过。本次真实 UE4 只验证相机存在的成功路径，没有让真实场景故意缺相机。场景内容校验、真实生成与 SearchTarget、物理收尾独立读数、F07/F08 正式规则均不在本次结论内。
