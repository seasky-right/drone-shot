# AirSim ReachPoint v0.2

`sample-airsim-v02.json` 是单机固定场景样例。`scenario.config.target` 是唯一的目标坐标来源，采用世界 NED 米；样例为 `(4, 0.5, -2)`。Backend 在 reset/takeoff 后调用 `simPlotPoints` 绘制绿色、120 秒、非持久的目标标记，并把同一坐标写入平台观测的 `state.target`。Agent 从观测导航，Task 用同一坐标和 `tolerance_m` 判定抵达。旧配置中的 Agent/Task `target` 可保留，但必须与场景目标一致；新配置无需重复填写。

在已启动、隔离的 UE4 AirSim 场景中，先检查车辆、出生点、home、目标和速度，再运行：

```powershell
D:\Anaconda\python.exe -m core.plugin_cli preflight builtin_pack/sample-airsim-v02.json --enable-backend
D:\Anaconda\python.exe -m core.plugin_cli run-multi builtin_pack/sample-airsim-v02.json --output runs --enable-backend
```

单次样例的 `episode_id` 固定；复跑前修改 ID 或选择新的输出目录。`sample-airsim-reach-benchmark-v02.json` 使用同一场景和目标做两次重复，Benchmark ID 自动生成：

```powershell
D:\Anaconda\python.exe -m core.plugin_cli run-benchmark builtin_pack/sample-airsim-reach-benchmark-v02.json --output runs --enable-backend
```

当前 Agent 是限速直达 baseline；`elapsed_wall_s` 可用于比较完成时间，不表示最短时间或最优路径。运行器记录抵达成功、观测间墙钟耗时、终点误差、按平台快照计算的路径长度、碰撞采样数/阳性数及收尾错误。`sampled_safe_success` 要求任务成功、所有已记录快照都有碰撞读数且均为阴性、收尾无错误。新 v0.2 流程在 move-to 控制循环中遇到 AirSim 碰撞阳性会停止动作，再尝试既有收尾。采样与控制循环检查都不是独立的连续安全认证；`cleanup_errors=[]` 也不是落地后的独立物理读数。

Benchmark 的 `success_rate_all_attempts` 和 `sampled_safe_success_rate_all_attempts` 以**已完成并返回结果**的案例为分母，失败状态仍在其中。若运行器异常中止，Benchmark 不产出完整汇总。两次相同目标只检查固定场景重复性，不支持跨场景或多目标排名。

本地只读回放可用：

```powershell
D:\Anaconda\python.exe -m core.console_viewer runs/<episode-id>
```

页面轨迹图和侧栏从公开 `state.target` 显示目标，RGB 是最新一次机载采样。2026-09-29 在隔离场景副本的单次真实试飞记录位于 `runs/real-airsim-goal-20260929/episodes/airsim-goal-visible-20260929/`：任务成功，终点误差 0.135 m，两个平台快照碰撞读数为阴性，收尾错误为零。真实 `simPlotPoints` RPC 调用未报错，但屏幕抓取未得到 UE4 窗口，机载 RGB 也不足以明确辨认标记，因此这次记录**未直接确认世界标记的视觉外观**。回放页面的目标显示另有 `viewer.png` 和 `viewer-mobile.png` 可查。
