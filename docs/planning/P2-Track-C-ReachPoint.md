# P2 Track C：ReachPoint 规则与样例

本实现只消费平台 v0.1 协议。 `TaskSpec.task_type` 为 `reach_point`，`parameters.target_position_ned` 是必填的世界 NED 米坐标对象。可选参数：

| 参数 | 默认 | 含义 |
| --- | --- | --- |
| `tolerance_m` | 0.5 | 动作后位置到目标的三维欧氏距离上限，含边界 |
| `require_hover_confirmation` | false | 若为 true，抵达后还须成功执行一次 `hover`，并以该动作后的观测确认位置 |
| `max_arrival_speed_mps` | 无约束 | 动作后三维速度模长上限，含边界 |
| `waypoints_ned` | 仅目标点 | 固定航线 Agent 的世界 NED 航点数组；最后一点必须在目标容差内 |

以上 0.5 米只是无仿真 Mock 样例默认值，不是 P3 真实场景的批准容差。P3 前须在 F08 中冻结实际航点、高度、动作时限、停留与验收参数。当前“停留”仅指 hover 动作后一次观测确认，不宣称持续若干秒；若需停留时长，须先扩展单调计时接口和验收规则。

`ReachPointTask.update` 根据动作后观测判断到达；执行失败返回 `backend_error`。未到达时保持未结束，由 Runner 按 `TaskSpec.time_budget_s` 的单调时钟预算终止为 `timeout`。Evaluator 输出任务成功标志、终点误差、最近距离和逐步观测点路径长度。路径长度只描述采样点折线，不代表真实飞行航程。

返航和降落由 `Backend.cleanup()` 实现，`CleanupResult` 独立于任务终止原因。返航或降落失败的 fixture 保留原始到点结果并记录收尾错误；P3 前须在 F08 决定安全收尾是否为整体验收硬条件。平台观测尚无碰撞数据，本评测不输出 `collision_count`，也不把缺失数据解释为零次碰撞。若 P3 验收要求无碰撞，必须先取得可信的碰撞数据和缺失时规则。

无仿真样例见 `fixtures/reachpoint/scenarios.json`；验证入口为 `python -m unittest tests.test_reachpoint_p2`。
