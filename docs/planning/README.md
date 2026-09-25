# 开发规划索引

更新日期：2026-09-25。

## 文档依据

1. **当前事实**：仓库代码、配置和可复查的验证结果决定“现在有什么”。PRD 中不符合现实的描述按现实修正，不能用规划代替实现证据。
2. **整体路线**：[综述.md](../../综述.md) 决定平台的发展方向。当前调整的是实现顺序：先完成可插拔 Core，再由其他 agent 按公开 Plugin Contract 开发具体能力；路线中的研究目标不因此视为已完成。
3. **活动执行**：[可插拔 Core 执行计划](可插拔Core执行计划.md)是当前工作顺序。原[近期开发执行计划](近期开发执行计划.md)保留 P0-P5 历史计划与进展，不再分配下一批任务。
4. **职责参考**：[Track A](../../track_prd/TRACK_A_PLATFORM_CORE.md)、[Track B](../../track_prd/TRACK_B_SIMULATOR_RUNTIME.md)、[Track C](../../track_prd/TRACK_C_TASK_BENCHMARK.md) 保留历史职责信息，不决定新的代码所有权或合流顺序。
5. **弃用材料**：[项目整体综述0805.md](../archive/项目整体综述0805.md) 已存档，不再参与路线或优先级判断。

## 阅读与使用顺序

| 文档 | 用途 |
| --- | --- |
| [可插拔 Core 执行计划](可插拔Core执行计划.md) | 当前 C0-C6 的依赖、交付物、验收和插件开发交接 |
| [Plugin Contract v0.2 开发说明](../plugins/README.md) | 候选扩展点、Manifest、安装运行、兼容矩阵与限制 |
| [插件开发交接](../plugins/插件开发交接.md) | 插件作者入口、十类组件实际运行范围、验证步骤和 Core 变更规则 |
| [可插拔 Core 阶段验证](../validation/可插拔Core阶段验证-2026-09-25.md) | 本轮实现、独立 wheel/外部插件证据与未完成关卡 |
| [插件任务单 v0.2 候选](插件任务单-v0.2候选.md) | 后续 Pack 的公开接口、能力和验收输入 |
| [项目现状报告](../validation/项目现状报告-2026-09-25.md) | 插件开发交接基线、单组件混装证据和未完成事项 |
| [切换时现状快照](../validation/项目现状报告-2026-09-24.md) | P3 与插件化改造前的实现状态 |
| [近期开发执行计划](近期开发执行计划.md) | 原 P0-P5 历史计划及当时进展 |
| [前置条件冻结清单](前置条件冻结清单.md) | 原 F01-F11 协议与真实任务验收条件；新 Core 的冻结关卡见活动计划 |
| [GitHub上传前仓库整理清单](GitHub上传前仓库整理清单.md) | 文件去向、保留项、排除项、环境和协作入口、上传检查 |

## 当前结论与证据范围

- [G0 决策](../decisions/2026-09-20-g0-platform-contract-v0.1.md)和现有代码是 v0.1 迁移起点，不等于 Plugin Contract v0.2 已确定；真人 track 确认仍未发生。
- P0/P1 的本地验收记录见 [P0-P1 报告](../validation/P0-P1验收报告.md)。2026-09-23，P2 A/B/C 代码及跨线 Mock ReachPoint 集成已完成，当时平台测试 39 项、底层适配测试 9 项通过；wheel 范围及独立安装后的 CLI 运行通过。细节见 [P2 实现验收报告](../validation/P2实现验收报告.md)。Track A 又独立推进至 V0.3 Mock 验收：批量实验、统计报告和记录重读已完成，当时平台测试 42 项、底层测试 9 项通过，3 Agent × 20 seeds 的 60 次 Mock episode 在独立 wheel 安装中跑通；见 [Track A V0.3 Mock 报告](../validation/Track-A-V0.3-Mock验收报告.md)。
- 2026-09-23 已获取 `origin/agent` 的提交 `9563c4d`，经集成分支接入并合入 main 的直达、固定航线和搜索导航 Agent 及基础 ReachPointTask。提交内容属于 Agent/Task，不含新的 AirSim Backend；两种新 ReachPoint Agent 已通过 Mock CLI/Runner/Recorder 集成验证，搜索任务闭环仍待 P4。见 [Agent 分支合并验收报告](../validation/Agent分支合并验收报告.md)。
- Track A 与 C 的正式 ReachPoint 消费者已完成 3 Agent × 20 seed 共 60 次 Mock 批量实验：均成功、均可重读，当前 `python -m pytest tests` 126 项通过（含底层适配测试 8 项）；见 [ReachPoint Mock 批量实验报告](../validation/Track-A-ReachPoint-Mock批量实验报告.md)。这不构成随机场景或真实 Benchmark 证据。
- B 已提供独立 AirSimBackend smoke 入口。2026-09-24 修复悬停三轴速度判定与传感器覆盖，并完成公开 CLI、Runner、Recorder、重读的 FakeRpc 本地闭环；144 项无仿真测试通过，wheel 独立安装后的 Mock 入口可运行。见 [P3 AirSim 本地接入修复报告](../validation/P3-AirSim-本地接入修复验收报告.md)。真实飞行、传感器和安全收尾仍是 P3 的独立验收，不以 FakeRpc 测试替代。
- 2026-09-24 已在真实 UE4 AirSim 场景完成独立 Backend smoke、固定航线 ReachPoint 成功 episode 及短动作期限可控失败 episode；两次完整记录均可重读，RGB/深度资源齐全，收尾成功，147 项无仿真回归通过。旧场景的 RPC 布尔返回值、出生点碰撞与 landed 标记有兼容限制；F07/F08 的正式确认、航程碰撞证据和第二 Agent 的验收口径仍待审查。见 [P3 AirSim 真实飞行验证报告](../validation/P3-AirSim-真实飞行验证报告.md)。
- 冻结基线当前校验未通过：manifest 中的 `PythonClient/.pytest_cache/` 六个缓存文件在本机缺失，其余 1,383 项内容匹配。报告记录了只读差异；不要为消除提示而运行 baseline writer。
- 本地 Git 仓库已存在；P2、Track A 与 Agent 分支集成已整理为可审查提交；上传范围与状态以 Git 历史和合并记录为准。
- 2026-09-25 Plugin Contract v0.2 候选、静态 Registry、Core/Pack 分离、双机 Mock、内置十类 v0.2 组件与仓库外插件已有技术验证；C0-C3、C5 技术自验通过。C4 使用同一安装版 Core/Pack 在本机 UE4 AirSim 固定航线完成成功与短期限可控失败真实回归，技术验收限于旧单机适配；C6 因受影响方确认缺失未通过。原十四个内置组件显式为 v0.1，不能计入 v0.2 conformance 通过数。准确命令、wheel 哈希和未闭合项见[阶段验证](../validation/可插拔Core阶段验证-2026-09-25.md)。没有真实双机飞行、其他引擎接入或新的 P3/F07/F08 人工确认。

## 状态维护规则

- 执行状态采用：待开始、进行中、待验收、已验收、阻塞。
- 协议状态采用：已有底层约定、建议待冻结、已冻结。必须有定义、样例、测试和确认记录才能标为已冻结。
- 完成工作包后，在对应行补上提交或 PR、验证结果和遗留问题；不要只改勾选框。
- C0-C6 的本轮状态只按新插件证据更新，不能把既有单机接口或旧 wheel 安装算作插件验收。旧 P2 实现、V0.3 Mock 基础设施与 P3 首次真实 ReachPoint 技术闭环保留为迁移回归证据；F07/F08 正式确认、P3 验收、SearchTarget 和正式 Benchmark 仍待后续插件工作与独立验收。
