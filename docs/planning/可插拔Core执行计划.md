# 可插拔 Core 执行计划

更新日期：2026-09-25。状态：实施中；C0-C3、C5 有技术自验证据，C4 的安装版单机真实 AirSim 迁移回归已通过，C6 缺受影响方确认。技术验证不等于协议冻结或 P3/F07/F08 正式验收。本文件是当前活动工作顺序。[原 P0-P5 计划](近期开发执行计划.md)保留为历史记录，[项目现状报告](../validation/项目现状报告-2026-09-24.md)记录切换时的代码和证据。代码与实测记录优先于计划。

## 1. 目标与边界

先交付可独立安装、通过公开 Plugin Contract 装配组件的基本 Core，再交由其他 agent 开发具体内容。原路线中的 Backend、Agent、Task、Scenario/Resource、Evaluator、Benchmark 定义与评分政策均应作为插件或插件包提供。Core 保留版本化协议、发现注册、依赖/配置/能力校验、Episode 运行、事件与上下文、Artifact/Result Store 和不依赖任务语义的实验编排。

“可插拔”指符合**已定义扩展点和协议版本**的新模块无需改动 `core/` 或 `contracts/`。全新的运行语义仍需版本化协议变更，不能承诺任意未来需求零修改 Core。Core 完成条件包括旧单机流程兼容、双机 Mock episode、独立场景组合、仓库外插件安装运行及结果重读；不包括真实双机飞行、完整 SearchTarget 或正式 Benchmark 成绩。外部 Python 插件验收只证明扩展性，不证明恶意代码隔离；未来用户上传执行仍需独立的进程/容器与权限设计。

| Core 持有 | 插件/插件包持有 |
| --- | --- |
| Contracts/Schemas、Manifest、Registry、能力协商、配置和依赖校验 | Backend、Agent、Task、Scenario/Resource Provider、Evaluator、Benchmark Pack |
| Episode 状态机、取消/超时/错误语义、事件和权限隔离的上下文 | 具体动作执行、传感器映射、场景装载、任务规则和算法 |
| Artifact/Result Store、通用批量调度、溯源和任务无关统计 | 案例、seed/reset 规则、指标公式、评分/排名政策和专用报告 |

原路线的内容按下列扩展点落地，不要求 Core 自行实现这些领域能力：

| 原路线内容 | 预留的公开扩展点 |
| --- | --- |
| 异构仿真、SITL/真机前验证 | Backend Pack；仿真进程启动、健康检查和资源分配由 Runtime Provider Pack 提供 |
| 空间任务、Agent、Benchmark | Task/Agent/Evaluator/Benchmark Pack；Core 只运行与保存可比较的原始结果 |
| GIS 场景、随机化、合成数据 | Scenario/Resource Provider 与 Scenario Generator Pack；生成物通过 Artifact Store 登记 |
| RL/其他训练方式 | 交互式 Episode Session 的 `reset/step/stop` API 及训练 Driver Pack；不把 PPO 等算法放进 Core |
| 多机协同 | 多载具 Episode/Agent 绑定协议及 Coordination Pack；具体通信与策略由插件实现 |
| Web/教学端、报告、失败分析与课程生成 | 稳定的配置/运行服务 API、事件订阅及只读 Result Processor Pack；界面和分析策略在 Core 外 |

`simulator_contract/` 是底层适配协议，不是平台 Observation。Core 不导入课程材料、AirSim RPC 或具体插件。冻结材料目录、`airsim-settings/drone.json` 和旧场景 manifest 的路径及字节不变，不运行 baseline writer。

## 2. 切换时的事实

- P0/P1 平台 v0.1 协议、P2 单机 Runner/Recorder、Mock/AirSimBackend 和 V0.3 Mock 批量设施已有本地证据。真实 UE4 AirSim 已记录一个 ReachPoint 成功例和一个可控失败例；F07/F08 与 P3 正式验收仍待确认。这些是迁移回归基线，不等于插件 Core 已实现。
- CLI/实验 CLI 当前直接导入具体组件；`ActionKind` 仅有 `move_to`/`hover`，`BackendConfig`、`StepRecord` 和 Runner 按单机设计。底层虽有 Capability，但平台级协商、插件 Manifest/Registry 和外部插件验收尚不存在。
- 原 P3 验收、P4 SearchTarget 与 P5 正式 Benchmark 在 Core 交接后作为插件工作继续办理。架构迁移不代替真实场景、安全规则、真值隔离或 seed/reset 的独立证据。

## 3. 工作顺序

| 关卡 | 交付物 | 依赖 | 退出证据 | 状态 |
| --- | --- | --- | --- | --- |
| C0 | Plugin Contract v0.2 候选及兼容决策 | v0.1 代码、fixtures、记录 | 版本化接口、合法/非法样例、迁移矩阵 | 技术自验通过；候选待确认 |
| C1 | Manifest、Registry、Discovery、配置/依赖校验 | C0 的插件身份和 API | 外部包可列出/装载，错误在运行前暴露 | 技术自验通过；候选待确认 |
| C2 | 可扩展 Action/Observation、多机、Scenario、能力协商 | C0；可与 C1 并行 | 双机 Mock、场景组合、能力正反例 | 技术自验通过；真实多机另验 |
| C3 | 生命周期、事件/上下文、Artifact/Result Store | C1 装配接口、C2 数据模型 | 终止与清理路径有完整可重读记录 | 已验收（无仿真范围） |
| C4 | 现有实现迁为 Pack，Core 去硬编码 | C1-C3 | 同一 Core wheel 可装配 Mock/AirSim/ReachPoint | 技术验收通过（本机单机真实场景范围）；F07/F08 另验 |
| C5 | Conformance Kit 与仓库外插件验收 | C1-C4 | 独立 wheel、validate-plugin、端到端证据 | 技术自验通过（v0.2 Mock/Contract 范围） |
| C6 | 协议确认、文档与交接 | C5 | 公开开发包、兼容矩阵、验收记录、插件任务单 | 文档已整理；确认未通过 |

C0-C6 是新的 Core 计划，不改写历史 P0-P5 状态。可由不同 agent 承担工作包；Core 集成人维护公共版本和关卡验收，插件作者只依赖公开 Contract/Conformance Kit。没有实际负责人或跨 track 确认时不填写虚构批准人。

2026-09-25 技术进展与逐关卡遗留见[阶段验证记录](../validation/可插拔Core阶段验证-2026-09-25.md)。C0 的候选协议、迁移矩阵、fixtures、配置默认值/相对路径规则均已有实现，待受影响方审查；C1/C2 的 Registry、组件 `requires` 自动能力协商、外部 wheel、双机 Mock 和硬取消预检正反例已复验。C3 的单机和双机 Session 共用生命周期机制，取消、超时、事件、Runtime 和清理路径已有 Mock/Fake 退出证据。C5 内置 Pack 的十类 v0.2 组件和外部样例都有可执行 case；原十四个内置组件显式为 v0.1，在 v0.2 报告中为 unsupported，不能计为 passed。C4 的安装版 Mock/FakeRpc 及本机 UE4 AirSim 固定航线成功/可控失败均已复验；这只覆盖旧单机适配组件，不证明真实双机或其他引擎。C6 缺受影响方确认。未填提交或 PR，因为本轮未创建提交或 PR。

## 4. 工作包

### C0：公共边界与版本

1. 定义插件类别、组件工厂签名、一个 Pack 提供多个组件的规则、最小生命周期钩子与 Core 可见类型；覆盖 Backend/Task/Agent/Evaluator/Scenario、Scenario Generator、Runtime Provider、训练 Driver 和 Result Processor 的最小扩展点。给出 Core → Contracts、插件 → Contracts、Backend → 底层适配的依赖图，并用检查禁止反向导入。
2. 分别版本化 Plugin API、平台数据 schema、插件版本、场景/评分版本。v0.1 记录保持可读；v0.2 不兼容字段显式升级。未知字段、旧配置和迁移失败均有确定规则，不静默猜测。
3. 定义配置中的组件 ID、Scenario、载具/Agent 绑定、资源限制、artifact 根目录；插件配置按公开 JSON Schema 校验。规定默认值、相对路径、秘密值不入记录及配置快照。

退出：决策记录、协议定义、有效/无效 JSON fixtures、边界测试齐全；另一实现者只读公开协议即可写最小组件。此时标为“候选”，未取得受影响方确认前不称已冻结。

### C1：插件发现、装配与预检

1. Manifest 必填 `id/version/type/plugin_api/entry_point/dependencies/capabilities/config_schema`；Pack 可声明多个稳定组件 ID。Manifest 作为已安装 distribution 的静态资源打包，通过 entry points 定位并在导入插件代码前读取/校验，之后才加载工厂；不执行配置中的任意导入字符串。
2. Registry 检查 ID 冲突、版本范围、依赖环、组件类别及配置 schema；解析顺序确定，失败附结构化原因和来源。只校验依赖，不在运行时自动安装包。真实 Backend 的显式启用保护继续保留。
3. CLI、实验入口和 Console 从 Registry 取得工厂，不再维护具体组件名称表。提供列出插件、预检配置、解释组合失败的命令；插件安装位置不影响发现。

退出：仓库外最小测试分发包安装后可列出并实例化；重复 ID、缺依赖、不兼容 API、坏 schema/配置均在 Backend 启动前失败；导入 Core 不会导入 AirSim 或 ReachPoint。

### C2：数据、场景和能力协议

1. Action 固定 `action_id`、目标 `vehicle_id`、命名空间 `kind`、`payload_schema`、JSON 安全 `payload`、时限和执行关联。具体参数由注册的动作 schema 与执行者校验，未知动作明确拒绝。区分控制动作、采样请求与任务上报的语义，不能把所有事件伪装为飞控命令。
2. 平台 Observation 保留序号、时刻、载具、公共状态、传感器/资源引用和缺失原因；扩展字段按命名空间及 schema 承载，二进制单独存储。Agent 可见数据与 Task/Evaluator 真值上下文分通道；不把低层传感器 Observation 混用为平台 Observation。
3. Episode 使用稳定 vehicle ID 映射状态；定义单机/多机快照、Agent 绑定、动作集合、逐动作结果、事件顺序、部分失败和载具缺失语义。双机 Mock 验证集中式及按载具绑定 Agent，真实 AirSim 双机不在本关卡内。
4. Scenario/Resource Contract 独立于 Task 和 Backend，声明场景 ID、版本/校验、资源、seed、初态和真值访问级别。Backend 报告装载/重置能力；同一 Urban 场景可分别组合 Search 与 Mapping Task。不能装载时在执行前拒绝。
5. 能力描述涵盖动作、传感器类型与实际资源、坐标/时间、最大载具数、场景操作、真值通道和有界执行。先匹配 Manifest，连接/资源发现后复核；“支持 RGB”不等于指定相机已存在。协商结论及降级入记录。

退出：Mock/Fake 测试覆盖可用/不可用的 Task × Scenario × Backend × Agent 组合；双机 episode、传感器缺失可序列化和重读，真值不进入 Agent 观察；旧单机组件经明确适配层继续工作。

### C3：生命周期、上下文与存储

1. Core 管理 `discover/load → validate → reset → start → step → stop → cleanup/close` 状态转换；`load` 是装配过程，不强迫所有插件实现同名方法。将可交互 Episode Session 的 `reset/step/stop` 作为公开服务接口，单次 Runner 和训练 Driver 共用其状态机。规定 hook 顺序、初始化失败、终止原因优先级及组件错误与清理错误分记；Runtime Provider 的资源取得/释放也进入此状态机。
2. 定义 episode/动作预算、单调时钟、取消信号和 Backend 有界调用责任。当前同步 RPC 无法由 Runner 硬抢占；超时后才返回须如实记录为逾时。要求硬取消的组合必须声明并验证可中断或隔离能力，否则预检拒绝。
3. 事件与上下文 API 提供 episode/action/vehicle 关联 ID、权限分隔及 artifact 写入接口；对外提供配置预检、运行调用、事件订阅和只读结果访问，供 CLI、训练 Driver、Web 适配器与 Result Processor 消费。Store 原子写最终结果，失败保留 `INCOMPLETE`，拒绝路径越界；记录插件/依赖版本、配置、能力协商、场景校验、seed 实际作用范围和错误。批量统计保留失败分母，不包含任务特定排名政策。

退出：无仿真测试覆盖成功、任务失败、能力不足、组件异常、动作/总预算超时、取消、部分载具失败、收尾失败和写盘失败；最小训练 Driver 可通过公开 Session 重复 reset/step，最小 Result Processor 可只读产物而不改 Core。每类终止都有可重读结果或明确不完整标记，cleanup 失败不覆盖原始终止原因。

### C4：迁移现有实现和安装边界

1. 把现有 Mock、AirSim、ReachPoint Task/Evaluator/Agent 和基础 Benchmark 定义包装成插件/Pack；保留已有行为与 F07/F08 未决项，不在迁移时擅改评分或安全标准。`reach_point.py`/`reachpoint.py` 双入口按 F08 另行归一，适配层须标明所用规则。
2. Core wheel 只携带 Core/Contracts 和必要通用依赖；组件可以仍在同一仓库开发，但独立构建分发。AirSim 插件可依赖底层适配包，Core 不直接依赖。CLI、ExperimentManager、Console 使用配置驱动的 Registry 装配，旧配置转换显式可检查。
3. 对照旧 v0.1 成功/失败 fixtures、Mock 批量记录重读及 FakeRpc AirSim 接入。真实仿真行为改变时另做 P3 回归并单独留证；无仿真测试不替代真实验收。

退出：仅安装 Core wheel 时无需具体组件即可导入和列出注册表；安装 Pack 后同一 Core wheel 运行 Mock ReachPoint，切换 AirSim 不改 Core 文件；旧记录可读，现有全量无仿真回归通过。

### C5：Conformance Kit 和外部插件

提供 `validate-plugin` 命令与 Python 测试入口，覆盖 Manifest、依赖/配置、声明能力与实际执行、生命周期、错误封装、序列化、artifact 引用、真值隔离和资源清理。既有成功样例，也有预期拒绝样例；不只检查类是否满足 `Protocol`。

在仓库外构建一个只依赖公开分发包的插件，至少提供新 Agent 或 Task、命名空间动作/扩展观察、相应的 Mock 动作处理以及可组合 Scenario。独立 wheel 安装后由**未修改的 Core CLI**发现、配置、完成双机 Mock episode 并重读结果。留存安装命令、包清单、配置、运行输出及失败例；卸载该插件不破坏 Core。这是可以交给其他 agent 并行开发的决定性门槛。

### C6：协议确认和交接

发布实际通过的 Contract、Manifest 样例、组件骨架、`validate-plugin` 用法、兼容矩阵、能力词表、动作 schema 注册方法、Scenario 资源规范及故障排查。决策记录注明维护人、受影响方确认、未支持范围及升级规则；没有确认时标为“候选/待确认”，不能因测试通过写成“已冻结”。

每个后续 agent 的任务单只给公开 Contract 版本、插件类型/ID、能力需求、可访问上下文、配置/资源、验收 fixtures 和独立分发入口，不要求修改 Core。建议首批任务为 AirSim Backend Pack、ReachPoint/Benchmark Pack、SearchTarget Pack、Scenario/Resource Pack 与多机协同 Pack；按扩展点分工，不沿用机械的 A/B/C 代码划分。P3 的 F07/F08、P4 的 F10、P5 的 F11 仍按[冻结清单](前置条件冻结清单.md)及真实证据分别验收。

## 5. 执行与变更控制

- 每关卡交付代码、样例、测试、独立安装/运行证据和已知限制；索引状态只在证据齐全后更新。破坏性协议变更先写版本迁移，再通知已开始的插件工作包。
- 常规验证使用无仿真 Contract/Mock/FakeRpc；真实 AirSim、传感器和安全收尾单独记录。不得为消除冻结基线中六个缓存文件缺失而运行 baseline writer。
- 不初始化新 Git 仓库、不上传或发布资源；未来上传先核对实际文件集。本计划不指定 GitHub 所有人、协作者、许可证或未发生的人为批准。
