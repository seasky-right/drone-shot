# Lawnmower Search Agent Plugin (v0.2 候选)

本插件提供基于平台候选契约 `drone.plugin.api/v0.2` 的独立往复式（Lawnmower / Boustrophedon）区域搜索 Agent 实现 `LawnmowerSearchAgentV02`。

## 一、组件规格与适用范围

- **Pack ID**: `drone.agent.spatial_search`
- **Component ID**: `drone.agent.spatial_search/agent`
- **Component Type**: `agent`
- **适用载具规模**: **单机环境与运行时单机约束**。公共清单契约不添加未经支持的 `vehicles` 字段；Agent 在运行时严格拒绝多机快照（若收到 `len(observations) != 1` 抛出 `ContractValidationError`），当前组合场景和 Backend 为单机。
- **支持动作**:
  - `spatial/move-to` (`spatial.move-to/v1`): 控制无人机飞往指定 NED 坐标。
  - `spatial/report-target` (`spatial.report-target/v1`): 向平台报告所发现目标的估计坐标与 ID（经 REPORT 通道）。
- **运行环境性质说明**:
  - 当前组件面向 `sample.spatial-search/backend` 实验性空间搜索 Mock 环境。
  - 该环境提供多通道动作与隐藏真值评分闭环，**不等于正式 SearchTarget 协议**，亦非基于 UE4/AirSim 的物理与相机感知环境。

## 二、算法与行为设计

### 1. 严格搜索区域校验与往复式航线生成
- **严格输入校验**：Agent 实时校验 `PlatformObservationV02.state["search_area"]`。若 `search_area` 缺失、缺少任一轴（`north`、`east`、`down`）、包含非有限浮点数（如 `NaN`、`Inf`）或坐标范围倒置（`min >= max`），Agent 立即抛出 `ContractValidationError`，**绝不静默降级为原地悬停**。
- **航线生成机制**：依据公开的搜索区域与配置的 `lane_spacing_m` 生成双精度数值安全的航带序列（无条件起始于 `min_east`、终止于 `max_east`，中间严格单调递增）。
- **环境安全性限定**：默认航带间距 `2.0m` 仅在当前固定的 6m×6m Mock 搜索区以及已测试的初态下能有效避开中心圆柱障碍走廊，**并不保证对任意未知地图或障碍物分布绝对安全**。

### 2. 主动障碍物航段安全检查
- Agent 支持通过配置项 `obstacles`（每个障碍物包含 `center_north_m`、`center_east_m`、`radius_m`）接收已知障碍物几何信息。
- **航段前置主动校验**：在规划与发出移动指令前，Agent 真正对“当前位置到首航点”以及所有后续相邻航点构成的线段进行障碍物安全距离检测（欧氏线段投影距离）。
- **拒绝危险动作**：一旦检测到任一规划航段穿过已知障碍物危险半径，Agent 在动作发出前立即抛出 `ContractValidationError` 明确拒绝执行，杜绝静默忽略配置或由仿真后端发生物理碰撞。

### 3. 目标检出、平台回显去重与有限重试
- **检出过滤**：监测 `observation.state["detections"]`，选择 `confidence >= min_confidence`（默认 0.5）且数据格式合法的候选目标。
- **平台回显去重（Echo-based Deduplication）**：Evaluator 将针对同一目标的重复上报记为误报（`false_positive_count`）。Agent 并不单一依赖内部局部记忆，而是以平台在观察状态中回显的已接收报告列表 `observation.state["reports"]` 为准：
  - **有限重试（限定于 Episode 继续运行的情况）**：当 Agent 发出 `spatial/report-target` 后，若该动作未被平台回显确认（例如动作未被登记到观察状态的 `reports` 回显中），且当前执行器/后端并未将该情况判定为致命失败并终止 Episode，同时目标仍处于视野检出范围内，Agent 允许在后续步骤进行有限次上报重试（由 `max_report_attempts` 控制，默认 3 次）；若执行器把动作失败直接判为终止（如 `succeeded=False` 导致 `PARTIAL_FAILURE` 中止），则当前局已结束，无法在同局内进行重试；
  - **严格去重**：一旦观察状态的 `reports` 列表中已包含该 `target_id`，Agent 立即严格抑制后续针对该目标的重复上报，转为到位保持或继续后续航带巡视。

### 4. 真值隔离（Truth Isolation）
- 严禁任何形式的真值窥探。若观察状态中出现 `truth` 或 `ground_truth`，Agent 立即抛出 `ContractValidationError`。

## 三、配置参数契约

| 参数名 | 类型 | 默认值 | 约束与说明 |
| :--- | :--- | :--- | :--- |
| `lane_spacing_m` | `float` | `2.0` | 往复航带间距，必须为有限正浮点数 |
| `min_confidence` | `float` | `0.5` | 目标检出置信度阈值，范围 $[0.0, 1.0]$ |
| `max_report_attempts` | `int` | `3` | 未获平台回显确认时的最大连续重试次数，整数 $\ge 1$（仅在局未终止时有效） |
| `waypoint_tolerance_m` | `float` | `0.3` | 到位判定三维容差，必须为有限正浮点数 |
| `action_deadline_s` | `float` | `1.0` | 单步动作超时阈值，必须为有限正浮点数 |
| `flight_down_m` | `float \| null` | `null` | 指定固定飞行下向深度，若为 `null` 则保持当前观察中的 `down_m` 坐标作为航行深度 |
| `obstacles` | `list \| null` | `null` | 已知障碍物几何参数列表，用于航段前置安全检查 |

## 四、测试与验证说明

1. **测试用例集** (`tests/test_lawnmower_search_plugin.py`):
   - 包含 16 项自动化单元与集成测试，覆盖清单发现、组件探针、单机约束、种子 7/11/19 运行、未回显重试与已回显去重、状态原子重置、主动碰撞拦截（反例断言）、搜索区域严格校验、多机环境单机运行以及与官方 Baseline 严格比对。
   - 所有测试均采用动态模块发现机制（`importlib.util.find_spec` 与相对路径），杜绝绝对路径硬编码。
2. **打包与安装状态声明**:
   - 插件 Wheel 包构建机制完整可用；
   - 因平台正式仓库 Core 当前配置为 `requires-python = "<3.14"`，在 Python 3.14 开发环境下 pip install 受版本标记拦截；Wheel 在 Python 3.13 独立安装环境和真实 AirSim 仿真环境下的状态均如实记录为**未验证**。
