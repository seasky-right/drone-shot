# Track A × Track C ReachPoint Mock 批量实验报告

日期：2026-09-23。实验编号：`exp-1693d8d0f78e4223974d206db27e04de`。本报告验证 Track A 的 ExperimentManager 可批量运行已合入的 Track C ReachPoint Agent/Task/Evaluator；所有 episode 使用 MockBackend，未运行真实 AirSim。

## 配置与复现

配置文件为 [`reachpoint.experiment.example.json`](../../reachpoint.experiment.example.json)。同一 TaskSpec、BackendConfig 和 seed 列表用于三个 Agent：`direct`、`fixed-route`、`legacy-route`；seed 为 0—19，每个组合运行一次，共 60 次。任务从世界 NED `(0, 0, 0)` 到 `(5, 0, -2)`，容差 0.5 米，并要求到点后一次成功的 hover 确认。速度上限 0.1 米/秒仅在 Mock 的零速度观测中验证。

运行命令：`drone-experiment --config reachpoint.experiment.example.json --output experiments/reachpoint-local`。本机生成的原始结果在 `experiments/reachpoint-local/exp-1693d8d0f78e4223974d206db27e04de/`，包含逐次 episode、`summary.json`、`metrics.csv` 和 `report.md`；`experiments/` 按仓库规则不上传。

## 结果

| Agent | 任务成功 | 基础设施错误 | 清理失败 | 每次动作数 | 采样路径长度均值 | 终点误差均值 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| direct | 20/20 | 0 | 0 | 2 | 5.385 米 | 0 米 |
| fixed-route | 20/20 | 0 | 0 | 4 | 5.828 米 | 0 米 |
| legacy-route | 20/20 | 0 | 0 | 3 | 10.875 米 | 0 米 |

三个 Agent 都按任务规则执行了最终 hover。本轮没有任务失败样本；失败分母和错误分类沿用此前一步 Mock 批量实验及 fixtures 的验证证据。60 个 episode 均有完整结果，使用 `drone-replay` 的同一读取/校验逻辑逐个重读后，成功和清理状态均为 60/60。按同一 seed 的 Agent 矩阵运行；新集成测试以 2 seed × 2 repeat 再验证共享配置、记录、动作数和指标顺序。本轮在合并后的 main（`ab5361f`）上重跑同一配置：`python -m pytest -q tests` 126 项通过（其中 `tests/test_airsim_backend.py` 8 项）、`python -B scripts/validate_reach_point_pack.py` 校验通过，60 次 episode 用 `drone-replay` 逐一重读均为成功且清理正常，指标与上表一致。更早一轮独立安装 wheel 时的核对给出了依赖锁、wheel 范围检查、`drone-experiment` 完成 60/60 次与核心模块从 `site-packages` 导入的结论。

`direct` 直达目标；`fixed-route` 消费 `intermediate_waypoints_ned`；`legacy-route` 消费 `waypoints_ned`。两份路线参数都对三个 Agent 可见，但路径预设不同，因此路径长度差异主要由预设航点决定，不能解释为算法性能差异。当前路径长度只是 Mock 离散观测点的折线距离；Mock 会瞬时更新位置，无动力学、避障、传感器噪声或场景随机化。20 个 seed 虽写入 TaskSpec，却没有改变场景，不能当作 20 个独立随机实验。本机 `elapsed_s` 约为毫秒级进程/写盘耗时，不是飞行完成时间。

## 对规划的影响

这完成了“Track A 通用批量执行 + Track C ReachPoint 消费者”的无仿真连接验证，比先前只用一步 Mock 替身的批量实验更进一步；仍不是 P3 真实闭环，也不是 P5 正式 Benchmark。下一批不依赖真实仿真的工作：A 统一 Mock/AirSim 配置选择、传感器文件的 episode 相对路径和运行资源元数据；C 合并两份 ReachPointTask/航点参数语义，明确 F08 的成功、停留、收尾和缺失碰撞数据规则。F08 的实际高度、容差、时间和安全判据须结合 B 的 F07 真实能力结果再冻结。P5 还需 F11 的 reset、seed 与比较规则。
