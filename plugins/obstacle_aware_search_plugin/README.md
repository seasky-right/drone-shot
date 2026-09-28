# Obstacle-Aware Search Agent Plugin (v0.2 候选)

本插件提供基于平台候选契约 `drone.plugin.api/v0.2` 的已知地图避障区域搜索 Agent 实现 `ObstacleAwareSearchAgentV02`。相比只能拒绝穿障航线的基线 Lawnmower Agent，本插件具备几何绕行规划能力，能够在遇到已知障碍物时自动规划无碰撞绕行航段并继续搜索。

## 一、组件规格与适用范围

- **Pack ID**: `drone.agent.spatial_obstacle_search`
- **Component ID**: `drone.agent.spatial_obstacle_search/agent`
- **Component Type**: `agent`
- **Plugin API**: `drone.plugin.api/v0.2`
- **Entry Point**: `drone_spatial_obstacle_search:create_agent`
- **适用载具规模**: **单机环境与运行时单机约束**。公共清单契约保持规范，不设未经支持的 `vehicles` 字段；Agent 在运行时严格拒绝多机快照（若收到 `len(observations) != 1` 抛出 `ContractValidationError`），当前组合场景和 Backend 为单机。
- **支持动作**:
  - `spatial/move-to` (`spatial.move-to/v1`): 控制无人机飞往指定 NED 坐标。
  - `spatial/report-target` (`spatial.report-target/v1`): 向平台报告所发现目标的估计坐标与 ID（经 REPORT 通道）。
- **运行环境性质说明**:
  - 当前组件面向 `sample.spatial-search/backend` 实验性空间搜索 Mock 环境。
  - 该环境提供多通道动作与隐藏真值评分闭环，**不等于正式 SearchTarget 协议**，亦非基于 UE4/AirSim 的物理与相机感知环境。

## 二、算法与行为机制设计

### 1. 规模边界预判与防爆算防护
为杜绝病态输入造成内存溢出或计算挂起，算法设有严格、可解释的工作量与规模硬上限：
- **航带规模预判 ($O(1)$ 快速拦截)**：在 `_generate_axis_coordinates` 中，计算前先执行预估：若 `(max_coord - min_coord) / step > MAX_LANE_COUNT`（上限 500 条航带），立即抛出 `ContractValidationError`，耗时 $< 0.1\text{ms}$，绝不进行海量循环或列表分配。
- **已知障碍物数量上限**：`MAX_OBSTACLES = 50`，超出上限时在初始化校验阶段立即拒绝。
- **可视图节点规模上限**：`MAX_GRAPH_NODES = 400`，防止密集障碍物场景下可视图节点组合爆炸。
- **A\* 搜索迭代硬上限**：`MAX_ASTAR_ITERATIONS = 1000`，确保绕行搜索必定在多项式时间内收敛，杜绝死循环。
- **总规划航点上限**：`MAX_PLANNED_WAYPOINTS = 2000`，防止极端折线航迹产生不可执行的长序列。

### 2. 障碍物安全边距与几何可视图 A* 绕行规划
- **安全边距配置**：针对配置的已知圆形障碍物，引入 `safety_margin_m`（默认 0.25m），形成有效避障半径 $R_{eff} = \text{radius\_m} + \text{safety\_margin\_m}$。
- **候选净空节点生成**：在每个障碍物周围生成 16 个等角间隔的净空候选节点（距离中心 $1.25 \times R_{eff}$），严格过滤掉位于 `search_area` 外部或与其他障碍物冲突的非法节点。
- **可视图与 A\* 搜索**：当航段直连线与任一障碍物冲突时，在起点、终点和净空节点构成的可视图上运行 A* 启发式搜索。所有添加的边均经过 `_segment_clear_with_obstacles` 严格判定，确保返回的绕行子路径各段欧氏距离恒大于 $R_{eff}$。
- **路径平滑**：采用贪心视线平滑算法消除冗余折线，生成平滑连续的折线绕行段。

### 3. 承诺航线语义：不可达时显式失败，杜绝伪造“全覆盖”
- **不可达显式报错**：算法坚决不进行静默跳过（Silent Dropping）。若搜索航带航点落在障碍物内部（无法进驻），或两航点之间因障碍物彻底封堵导致 A* 无法找到安全连通路径，Agent 立即抛出 `ContractValidationError`，并明确指出受阻坐标与原因（如 `Unreachable waypoint (6.0, 3.0): obstacle at (6.0, 3.0) with radius 0.50m prevents access`）。
- **语义准确性承诺**：本 Agent 不虚假声称能“无视阻断实现 100% 完整覆盖”；其契约承诺为：**对所承诺生成的航段与绕行段进行数学上的无碰撞验证；一旦存在不可达盲区，立即显式失败交由上层调度处理，杜绝掩盖盲区假装完成**。

### 4. 双重前置与运行时安全防线
- **规划期全局校验**：在根据 `search_area` 生成初始航线时，检查“当前位置到首航点”及所有相邻航段与绕行航段。不仅验证航点自身，且对航迹中的每一个移动线段执行全障碍物安全间距校验。
- **运行时动作前预检**：在每个 `act()` 周期发出 `spatial/move-to` 前，再次对 `curr_pos -> target_pt` 进行障碍物距离判定。若检测到潜在冲突，触发局部重规划；若无法安全通过则立即拦截。
- **能力边界声明**：在未配置已知障碍物或遭遇未知障碍物时，Agent 只能声明自身基于开环规划的能力限制，**不承诺在未知复杂地图中“绝对安全”**。

### 5. 严格搜索区域与深度范围校验
- 严格校验 `PlatformObservationV02.state["search_area"]`。若 `search_area` 缺失、缺少任一轴（`north`、`east`、`down`）、包含非有限浮点数（`NaN`/`Inf`）或坐标范围倒置（`min >= max`），Agent 立即抛出 `ContractValidationError`。
- 若显式配置了 `flight_down_m`，校验其必须严格位于 `search_area["down"]` 的深度区间 $[min\_down, max\_down]$ 内，禁止越界飞行。

### 6. 目标检出、平台回显去重与有限重试
- **检出过滤**：监测 `observation.state["detections"]`，选择 `confidence >= min_confidence`（默认 0.5）且数据格式合法的候选目标。
- **平台回显去重**：以平台在观察状态中回显的已接收报告列表 `observation.state["reports"]` 为准：
  - **有限重试（限定于 Episode 继续运行的情况）**：当 Agent 发出 `spatial/report-target` 后，若该动作未被平台回显确认，且当前执行器/后端并未将该情况判定为致命失败并终止 Episode，同时目标仍处于视野检出范围内，Agent 允许在后续步骤进行有限次上报重试（由 `max_report_attempts` 控制，默认 3 次）；若执行器把动作失败直接判为终止，则当前局已结束，无法同局重试；
  - **严格去重**：一旦观察状态的 `reports` 列表中已包含该 `target_id`，Agent 立即严格抑制后续针对该目标的重复上报，转为到位保持或继续后续航带巡视。

### 7. 真值隔离（Truth Isolation）
- 严禁任何形式的真值窥探。若观察状态中出现 `truth` 或 `ground_truth`，Agent 立即抛出 `ContractValidationError`。

## 三、配置参数契约

| 参数名 | 类型 | 默认值 | 约束与说明 |
| :--- | :--- | :--- | :--- |
| `lane_spacing_m` | `float` | `2.0` | 往复航带间距，必须为有限正浮点数，预估航带数不得超过 500 |
| `safety_margin_m` | `float` | `0.25` | 附加在障碍物半径外的安全间距（米），$\ge 0.0$ |
| `min_confidence` | `float` | `0.5` | 目标检出置信度阈值，范围 $[0.0, 1.0]$ |
| `max_report_attempts` | `int` | `3` | 未获平台回显确认时的最大连续重试次数，整数 $\ge 1$（仅在局未终止时有效） |
| `waypoint_tolerance_m` | `float` | `0.3` | 到位判定三维容差，必须为有限正浮点数 |
| `action_deadline_s` | `float` | `1.0` | 单步动作超时阈值，必须为有限正浮点数 |
| `flight_down_m` | `float \| null` | `null` | 指定固定飞行下向深度，必须落在 `search_area["down"]` 深度区间内；若为 `null` 则保持当前观察中的 `down_m` |
| `obstacles` | `list \| null` | `null` | 已知障碍物几何参数列表（包含 `center_north_m`, `center_east_m`, `radius_m`，数量 $\le 50$），用于几何可视图绕行规划 |

## 四、同场景对比实验与验证

### 1. 典型对比案例 (Seed 7, `lane_spacing_m = 3.0m`, 中央障碍物 `(3, 3, r=0.75)`)
- **场景特征**：在 6m×6m 地图中，无人机自 `(0.0, 3.0)` 出发，航带间距 3.0m 会产生穿过中心障碍物的南北向航线；目标位于 `(6.0, 6.0)`。
- **Baseline Lawnmower**：航带直接穿越障碍物，在配置障碍物时因安全预检直接拒绝发出动作（`ContractValidationError`）；未配置障碍物时直接撞上后端被拒绝，无法到达目标。
- **ObstacleAwareSearchAgentV02**：
  1. 准确识别障碍物冲突，规划切线绕行节点 `(3.88, 3.88)` 与 `(2.52, 4.15)`；
  2. 绕行路径与障碍物中心最小距离为 1.25m（大于物理半径 0.75m 与安全边界 1.0m）；
  3. 安全绕行后进入目标视野范围，成功发出 `spatial/report-target`；
  4. 任务以 `SUCCESS` 终态完成，得分 1.0，误报数 0.0，碰撞拒绝数 0。

### 2. 离线可视化成果
插件根目录下提供以下路线示意成果：
- [`route_visualization.svg`](./route_visualization.svg)：标准独立矢量图，可直接在浏览器或图片查看器中打开；
- [`route_visualization.html`](./route_visualization.html)：包含矢量图、数据对比表与参数说明的离线 HTML 页面。

## 五、未来视觉模型接入所需公开图像接口与权限分析

为实现未来接入多模态视觉模型（如 YOLO、SAM、视觉大语言模型）进行真实机载目标检测，必须在平台架构上设计规范、安全、无特权的公开图像读取接口，当前分析如下：

1. **当前现状与缺口**：
   - 当前 AirSim 后端将相机拍摄的 PNG 落地到本地运行目录中的 Artifact Store，但观察状态中并未向 Agent 传递安全的图像读取句柄；
   - 当前空间搜索 Mock 则通过 `state["detections"]` 直接给出带误差的检测框与置信度，跳过了真实视觉计算。
2. **所需公开接口规范建议 (`SensorArtifactReader`)**：
   - **禁止直接传递本地绝对路径**：若直接在 `observation.state` 中暴露 `D:\.../camera.png`，会导致 Agent 代码与本地文件系统耦合，且破坏沙箱隔离；
   - **推荐注入只读上下文 Reader**：通过 Runner 在实例化或调度时注入受限的只读资源读取器（例如 `context.artifacts.read_bytes(resource_id: str) -> bytes` 或 `ObservationResourceResolver`）；
   - **受限生命周期**：仅允许读取当前 Step 或当前 Episode 的机载相机 Artifact，禁止跨 Episode 遍历文件系统。
3. **权限与密钥安全规范**：
   - **绝不在 TaskSpec / 公共契约中明文存储 API Key**；
   - 视觉推理应封装在环境侧的独立 Vision Provider 或由 Agent 从外部受保护的环境变量/密钥管理服务读取，严格杜绝将用户商业密钥提交至版本控制或序列化快照中。

## 六、环境与真实 AirSim 状态声明

> [!CAUTION]
> 1. **当前 Spatial Mock 验证通过**：本插件已在 `sample.spatial-search` 环境下完成了 19 项专项测试与基准测试验证；
> 2. **真实 AirSim 仿真未验证**：当前空间搜索基于纯 Python 的空间网格与离散报告通道，**未在真实 UE4/AirSim 实例下运行验证**；
> 3. **Python 3.13 独立安装未验证**：开发测试环境为 Python 3.14.4，因 Core 的 `requires-python = "<3.14"` 限制，Wheel 独立安装在 3.13 下暂未验证。
