# 开发规划索引

更新日期：2026-09-23。

## 文档依据

1. **当前事实**：仓库代码、配置和可复查的验证结果决定“现在有什么”。PRD 中不符合现实的描述按现实修正，不能用规划代替实现证据。
2. **整体路线**：[综述.md](../../综述.md) 决定平台的发展方向。近期展开 V0.1—V0.3；后续阶段暂不细化实现承诺。
3. **近期执行**：下列三份文档将整体路线拆为工作包、冻结条件和上传准备；属于执行规划，不表示这些工作已经完成。
4. **职责参考**：[Track A](../../track_prd/TRACK_A_PLATFORM_CORE.md)、[Track B](../../track_prd/TRACK_B_SIMULATOR_RUNTIME.md)、[Track C](../../track_prd/TRACK_C_TASK_BENCHMARK.md) 保留职责边界，已在文首说明当前偏差。
5. **弃用材料**：[项目整体综述0805.md](../archive/项目整体综述0805.md) 已存档，不再参与路线或优先级判断。

## 阅读与使用顺序

| 文档 | 用途 |
| --- | --- |
| [近期开发执行计划](近期开发执行计划.md) | 先做什么、由谁负责、依赖什么、如何验收 |
| [前置条件冻结清单](前置条件冻结清单.md) | 各合流节点之前必须确定的语义、接口、证据及负责人 |
| [GitHub上传前仓库整理清单](GitHub上传前仓库整理清单.md) | 文件去向、保留项、排除项、环境和协作入口、上传检查 |

## 当前结论与证据范围

- 三 track 分工继续以已实现的 [G0 决策](../decisions/2026-09-20-g0-platform-contract-v0.1.md) 和代码为共同接口；真人 track 确认仍未发生。
- P0/P1 的本地验收记录见 [P0-P1 报告](../validation/P0-P1验收报告.md)。2026-09-23，P2 A/B/C 代码及跨线 Mock ReachPoint 集成已完成，当时平台测试 39 项、底层适配测试 9 项通过；wheel 范围及独立安装后的 CLI 运行通过。细节见 [P2 实现验收报告](../validation/P2实现验收报告.md)。Track A 又独立推进至 V0.3 Mock 验收：批量实验、统计报告和记录重读已完成，当时平台测试 42 项、底层测试 9 项通过，3 Agent × 20 seeds 的 60 次 Mock episode 在独立 wheel 安装中跑通；见 [Track A V0.3 Mock 报告](../validation/Track-A-V0.3-Mock验收报告.md)。
- 2026-09-23 已获取 `origin/agent` 的提交 `9563c4d`，经集成分支接入并合入 main 的直达、固定航线和搜索导航 Agent 及基础 ReachPointTask。提交内容属于 Agent/Task，不含新的 AirSim Backend；两种新 ReachPoint Agent 已通过 Mock CLI/Runner/Recorder 集成验证，搜索任务闭环仍待 P4。见 [Agent 分支合并验收报告](../validation/Agent分支合并验收报告.md)。
- Track A 与 C 的正式 ReachPoint 消费者已完成 3 Agent × 20 seed 共 60 次 Mock 批量实验：均成功、均可重读，当前 `python -m pytest tests` 126 项通过（含底层适配测试 8 项）；见 [ReachPoint Mock 批量实验报告](../validation/Track-A-ReachPoint-Mock批量实验报告.md)。这不构成随机场景或真实 Benchmark 证据。
- B 已提供独立 AirSimBackend smoke 入口，但本轮未启动真实仿真器。真实飞行、传感器和安全收尾是 P3 的独立验收，不以 FakeRpc 测试替代。
- 冻结基线当前校验未通过：manifest 中的 `PythonClient/.pytest_cache/` 六个缓存文件在本机缺失，其余 1,383 项内容匹配。报告记录了只读差异；不要为消除提示而运行 baseline writer。
- 本地 Git 仓库已存在；P2、Track A 与 Agent 分支集成已整理为可审查提交；上传范围与状态以 Git 历史和合并记录为准。

## 状态维护规则

- 执行状态采用：待开始、进行中、待验收、已验收、阻塞。
- 协议状态采用：已有底层约定、建议待冻结、已冻结。必须有定义、样例、测试和确认记录才能标为已冻结。
- 完成工作包后，在对应行补上提交或 PR、验证结果和遗留问题；不要只改勾选框。
- 近期 P2 A/B/C 实现已有本地证据；Track A 的 V0.3 Mock 基础设施可独立验收。P3 真实合流、F07/F08 现场参数冻结和 P5 正式 Benchmark 仍待实施。
