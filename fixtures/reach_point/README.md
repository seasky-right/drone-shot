# ReachPoint C1 规则与验收

本目录完成《无人机智能体评价系统与 Benchmark 构建规划书》的 C1：固定任务参数、ReachPoint 评分规则、八类无仿真样例和单元测试。`c1_cases.json` 是可复核的输入及预期值，不代表已运行 AirSim。

## 任务参数

在 `TaskSpec` 中使用 `task_type=reach_point`，`time_budget_s` 为正数秒，`parameters` **恰好**包含：

| 字段 | 含义 |
| --- | --- |
| `target_position_ned` | 与 `PlatformObservation.position_ned` 同一世界 NED 坐标系、单位米 |
| `tolerance_m` | 严格正数，到达条件为三维欧氏距离 `<= tolerance_m` |
| `require_return_home` | 是否要求 `return_home_reached` 事件 |
| `require_landing` | 是否要求 `landed` 事件 |
| `collision_policy` | `zero`：碰撞或越界记零分但保留排名资格；`disqualify`：记零分并取消排名资格 |

这组样例固定目标 `(10, 0, 0)`、容差 `0.5 m`、预算 `10 s`。这些数值只用于 C1 样例；真实场景的航点、高度、速度和容差须在首次联调前另行确认并写入配置。首版无停留时间要求。

## 计算规则

- `ReachPointTask` 只根据动作后的实际观测判定到达；飞控命令的目标坐标或执行成功标志不能代替位置观测。命令失败单独标为 `backend_error`。
- `ReachPointEvaluator.evaluate(steps, progress)` 保持 G0 契约，提供 FR-EVL-01 的 `goal_reached`、`goal_score`、最终/最近距离和路径长度；无动作后观测时不能判定到达。
- `evaluate_episode(...)` 是 C1 的完整纯计算入口，额外接收初始观测、每步由 Runner 单调时钟提供的 elapsed 秒、终止原因、事件及 cleanup。首次达到目标的时间为 `completion_time_s`。墙上时间与模拟器时间不参与计时。
- 目标分是 `70` 或 `0`，同时要求动作后到达和 Task 报告成功。任务成功、在预算内到达且安全通过时，时间分为 `30 × max(0, 1 − completion_time_s / time_budget_s)`，总分为目标分加时间分。未到达、超时或其他终止原因总分为零。原始指标仍保留。
- 碰撞、越界、必要的返航/降落事件缺失，或必要安全收尾失败时，总分为零。`collision_policy` 决定碰撞/越界零分是否仍有排名资格。必要的返航/降落失败取消排名资格。
- `events=None` 表示安全事件数据不可用，`collision_count=None` 且取消排名资格；空事件列表表示记录系统完整工作且没有事件。只有确认事件采集完整时才能传空列表，不能把能力缺失写成零碰撞。

`goal_score` 是目标达成原始分，可能在安全失败时仍为 70；`score` 才是最终成绩。`safety_status` 为 `passed`、`failed` 或 `unavailable`。本地离线记录和 `result.json` 已实现，见[本地评价说明](../../docs/evaluation/本地评价使用说明.md)；事件来源及完整性、Runner 接口仍需在真实合流时冻结。

## 本地检验

在仓库根目录用 Python 3.13 执行：

```powershell
python -B -m unittest discover -s tests -p test_reach_point_c1.py -v
python -B -m unittest discover -s tests -v
```

第一条校验八类样例的原始指标、目标分、时间分、安全状态和最终成绩；第二条检查与既有平台契约及 Mock 链路是否冲突。测试无需 AirSim、UE 场景或网络。
