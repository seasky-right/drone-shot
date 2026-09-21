# Track B — Simulator Runtime

> 适用说明（2026-09-20）：本 PRD 保留职责与目标描述；与当前实现不符时，以仓库代码和验证记录为准。整体路线以 [综述.md](../综述.md) 为基准，近期执行以 [近期开发执行计划](../docs/planning/近期开发执行计划.md) 为准。当前已有 `AirSimLegacyAdapter`，尚无本文要求的完整 `AirSimBackend`；reset、move_to、return_home 及降落兼容仍需补齐并验证。原材料目录已纳入冻结清单，本文 `legacy/` 是目标概念，不授权直接改名搬移。首次真实合流先支撑 ReachPoint。

> 负责人：仿真与 Backend 开发  
> 目标：把 AirSim / 后续 Project AirSim、Isaac 等仿真器封装成稳定、统一、可替换的 SimulatorBackend。

## 1. 责任边界

本 Track 负责：

- 旧 AirSim API 迁移；
- `AirSimBackend`；
- 飞行控制；
- 状态读取；
- RGB / Depth / 传感器读取；
- 坐标和数据类型转换；
- reset；
- 异常与 timeout；
- Backend 稳定性；
- 后续 Headless / 仿真进程管理；
- 后续新增 Project AirSim / Isaac Backend。

本 Track 不负责：

- EpisodeRunner；
- Task 逻辑；
- Benchmark 指标；
- Agent 算法；
- Experiment Manager。

原则：**只实现 SimulatorBackend Contract，不关心上层是谁调用。**

---

# 2. 输入 / 输出边界

输入：

```text
TaskSpec
Action
BackendConfig
```

输出：

```text
Observation
EpisodeEvent
BackendError
```

本 Track 内部允许使用：

```python
airsim.xxx
```

本 Track 外部不得暴露 AirSim 特有对象。

---

# 3. V0.1 — AirSimBackend

## 3.1 冻结旧实现

保留当前已经跑通的原始 AirSim 控制代码作为：

```text
legacy/
```

用途：

- 回归；
- 对照；
- 故障排查。

不继续直接在 legacy 中堆平台功能。

## 3.2 实现 AirSimBackend

至少实现：

```text
connect
reset
observe
execute
close
```

### Action 映射

支持：

- takeoff
- land
- hover
- move_to
- move_velocity
- capture
- return_home

将统一 Action 翻译为 AirSim RPC。

### Observation 映射

至少输出：

- timestamp
- position
- orientation
- velocity
- vehicle state
- RGB
- Depth
- GPS / 可获得定位信息

将 AirSim 原始结构转换为平台统一结构。

## 3.3 坐标规范

必须明确并写入文档：

- AirSim 内部坐标系；
- 平台统一坐标系；
- xyz 轴方向；
- altitude 定义；
- 单位；
- orientation 表示；
- 后续 GIS / WGS84 转换接口预留。

严禁让其他模块直接猜 NED / ENU。

## 3.4 Backend Smoke Test

本 Track 自带独立测试脚本：

```text
connect
→ reset
→ takeoff
→ move
→ hover
→ capture RGB / Depth
→ return
→ land
→ close
```

不需要 EpisodeRunner 即可执行。

## V0.1 本 Track 验收

原有：

```text
起飞 → 飞行 → 拍照 → 返航 → 降落
```

完整通过 `AirSimBackend` 调用。

上层测试脚本中不出现 `airsim.xxx`。

---

# 4. V0.2 — Backend 稳定化

## 4.1 Reset

建立稳定 reset 流程：

- vehicle reset；
- API control；
- arm；
- 初始姿态；
- 初始位置；
- sensor state；
- 必要等待。

reset 后应具有可预期状态。

## 4.2 错误统一

将底层错误转换为平台错误，例如：

```text
BackendConnectionError
BackendTimeout
VehicleNotFound
SensorUnavailable
InvalidAction
ExecutionFailed
```

不得把 MessagePack / AirSim exception 直接传播到上层。

## 4.3 Action 稳定性

对以下动作反复测试：

- takeoff；
- waypoint；
- hover；
- capture；
- return；
- land。

建立：

- timeout；
- retry；
- cancel；
- safe fallback。

## 4.4 Observation 稳定性

保证：

- position / pose 数据格式稳定；
- RGB / Depth 类型稳定；
- 缺失传感器能够明确报告；
- 图像与姿态至少具有一致 timestamp / sequence 语义。

## V0.2 本 Track 验收

连续执行多个独立任务，不因前一次任务状态污染下一次任务。

---

# 5. V0.3 — Benchmark Runtime Reliability

V0.3 本 Track 不负责统计指标，而负责让真实 Backend 能支撑 Benchmark。

## 5.1 重复 Episode

目标：

```text
reset
run
reset
run
...
```

至少连续运行多次不出现明显状态积累。

## 5.2 Seed / Reset 支持

若仿真器可控制：

- vehicle spawn；
- target spawn；
- sensor noise；
- environment state；

则通过统一 config 暴露。

不能控制的随机因素必须记录。

## 5.3 Runtime Metadata

向 Recorder 提供：

- simulator type；
- simulator version；
- scene；
- backend version；
- vehicle config；
- sensor config；
- runtime errors。

用于 Benchmark 可复现。

## 5.4 性能基础记录

记录：

- observation latency；
- action latency；
- image capture latency；
- reset duration。

这里只负责提供原始 runtime 信息，不负责最终报告统计。

## V0.3 本 Track 验收

真实 AirSimBackend 可以稳定支撑一批重复 episode，供 ExperimentManager 调度。

---

# 6. 与其他 Track 的依赖

## 对 Track A

只遵循：

```text
SimulatorBackend
Observation
Action
EpisodeEvent
```

不依赖 EpisodeRunner 内部实现。

## 对 Track C

无直接代码依赖。

如果 Task 需要新的 observation 字段，应通过 Contracts 变更提出，而不是让 Task 直接调用 AirSim。

---

# 7. 合流节点

## Integration 0

共同确认 Contracts 和坐标规范。

## Integration 1

将：

```text
AirSimBackend
```

接入 Platform Core 的 EpisodeRunner。

需要证明：

```text
Runner 不修改核心逻辑
```

即可从 MockBackend 切换到 AirSimBackend。

## Integration 2

支撑 V0.3 Benchmark 连续实验。

---

# 8. 后续责任延续

V0.4 以后，本 Track 继续负责：

- 自动启动 / 关闭仿真进程；
- Headless；
- process health check；
- crash recovery；
- Project AirSim Backend；
- Isaac / Pegasus Backend；
- PX4 / ArduPilot SITL；
- 多 Vehicle Runtime；
- HIL / Sim-to-Real 底层适配。

本 Track 始终只负责“世界怎么被平台稳定控制和观察”。
