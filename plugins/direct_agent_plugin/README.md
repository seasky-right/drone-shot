# DirectPointAgent Plugin (v0.2 Candidate)

## 1. 概述与组件信息

本插件是将平台现有 `DirectPointAgent` 核心导航逻辑适配至候选 `drone.plugin.api/v0.2` 的最小独立 Agent 插件。

- **Distribution Name**: `drone-agent-direct-point`
- **Pack ID**: `drone.agent.direct`
- **Component ID**: `drone.agent.direct/agent`
- **Plugin API**: `drone.plugin.api/v0.2`
- **Component Type**: `agent`
- **Entry Point**: `drone_direct_agent:create_agent`
- **Declared Action Kinds**: `["drone/move"]`
- **Required Action Kinds**: `["drone/move"]`

## 2. 行为特征与设计说明

1. **确定性无大模型依赖**：基于规则的闭环导航算法，不需要任何付费大模型 API，不需要联网调用。
2. **多模式兼容**：同时支持 `CentralAgent` 协议（处理 `EpisodeSnapshotV02`，返回多机动作序列）与 `VehicleAgent` 协议（处理 `PlatformObservationV02`，返回单机动作），完美支持单机与多机集中绑定。
3. **状态闭环与步长推进**：
   - 读取观察数据中的 `north_m` 坐标；
   - 支持通过 `step_size_m` 进行增量步进（如每次推进 2.0 米），或省略 `step_size_m` 直接一次性下发目标点；
   - 到达目标容差 `tolerance_m` 范围内后，自动稳定在目标位置，满足内置 `drone.v02.mock/task` 的完成条件。
4. **Mock Backend 适配与往复搜索（Lawnmower）缺口**：
   - 内置 `drone.v02.mock/backend` 仅支持单一动作 `drone/move` 与单一坐标轴 `north_m`（其 `east_m` 与 `down_m` 均硬编码为 0.0，且无相机传感器）。
   - 现阶段 Mock 能力足以闭环验证目标点移动与到达保持。
   - `LawnmowerSearchAgent` 若要接入 v0.2，尚缺平台与 Mock 对以下能力的支持：
     - 二维/三维动作载荷（如 `drone.move/v1` 增加 `east_m`、`down_m` 或多航点协议）；
     - `drone/hover` 动作类别或通用的到位悬停语义；
     - 传感器输出（RGB/Depth Camera）及目标探测/上报通道。

## 3. 配置参数 Schema

```json
{
  "target_north_m": 5.0,
  "step_size_m": 2.0,
  "tolerance_m": 0.1,
  "action_deadline_s": 1.0
}
```

## 4. 运行与验证命令

```powershell
# 1. 发现与预检
drone-plugins preflight plugins/direct_agent_plugin/mixed-run.json

# 2. 插件 Conformance 探针校验（实际执行 act hook）
drone-plugins validate-plugin --component-cases plugins/direct_agent_plugin/component-cases.json --output <runs-dir>

# 3. 组合执行 Multi-Vehicle Episode
drone-plugins run-multi plugins/direct_agent_plugin/mixed-run.json --output <runs-dir>

# 4. 重读运行记录
drone-plugins show-result direct-agent-builtin-mock --output <runs-dir>
```
