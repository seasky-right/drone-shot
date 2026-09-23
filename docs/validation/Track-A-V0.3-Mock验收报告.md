# Track A V0.3 Mock 阶段验收报告

日期：2026-09-23。范围：在 P2 平台基线上继续实现 Track A 的批量实验、通用统计、结果报告和记录重读；Track B/C 的具体行为在本轮由 Mock 替身提供。本报告是无仿真本地证据，不是 P3/P5 真实仿真验收。

## PRD 覆盖位置

| Track A PRD 阶段 | 当前实现与证据 | 仍待完成 |
| --- | --- | --- |
| V0.1 公共协议与 Mock | 平台 v0.1 协议、MockBackend、Agent/Task/Evaluator 替身可独立调用。 | 原 PRD 列举的起飞、降落、速度、采集、返航等 Action 未纳入已冻结 v0.1；当前仅 `move_to`/`hover`，扩展须先改协议并跨 Track 确认。 |
| V0.2 Episode Runtime | Runner、Recorder、单次 Mock CLI 与成功/失败/超时/清理记录已在 P2 验证。 | 同一任务在真实 AirSim 上的完整 episode、传感器文件与 episode 目录关联仍待 P3 验收。 |
| V0.3 Experiment / Benchmark Infrastructure | 新增通用 ExperimentManager、每次尝试清单与已运行 episode 结果、数值统计、CSV/JSON/Markdown 报告和只读 replay；Mock 版 3 Agent × 1 Task × 20 seeds 已跑通。 | 正式真实任务比较须通过 P3、冻结 F11，并验证场景 reset 与 seed 的实际控制范围。 |

Track A PRD 的正式 V0.3 合流还需要真实 Backend、真实 Task/Evaluator 和多 baseline Agent；本轮只验收 A 自有的 Mock 基础设施。V0.4 及以后由 `综述.md` 的后续路线决定，不属于本轮实现。

## 本轮实现

- `core/experiment.py` 对每个 Agent、seed、repeat 新建一组组件与 EpisodeRunner；保存完整配置、逐次结果、失败和清理状态。平台不硬编码 AirSim 或 ReachPoint。
- `core/experiment_cli.py` 使用 MockBackend、一步位置 Task/Evaluator 及三种 Mock Agent，`experiment.example.json` 给出 20 个 seed 的可运行样例。
- `core/replay.py` 只读取和校验 result、trajectory、events 及传感器文件引用，输出位置序列；不向仿真器重放动作。
- 汇总按 Agent 给出任务成功率、episode 分母、基础设施错误数、收尾失败数、终止原因及各数值指标的 count、missing_count、mean、median、std、min、max。失败 episode 留在分母；缺失指标不补零。

## 验证

- `python -B -m unittest discover -s tests -q`：**42/42 通过**；底层 `simulator_contract/tests`：**9/9 通过**。
- 本地 wheel 范围检查通过；仓库外的独立虚拟环境安装 wheel 后，`drone-experiment` 生成 60 个 Mock episode，`drone-replay` 能读取其中的记录，模块从 `site-packages` 导入。
- 60 次结果：`fixed` 20/20 任务成功，`hover` 0/20，`offset` 0/20。后两者是有意设计的 Mock 失败样例，用来检验失败分母与指标；它们不能用于判断真实 Agent 性能。

## 解释边界与后续

每个 seed 被写入对应 episode 的 `TaskSpec.seed`，但 MockBackend 没有场景随机化；20 个 seed 不代表 20 个独立随机场景。当前 replay 是记录重读和完整性检查，不承诺物理仿真精确重演。同步 Backend 调用仍无法由 Runner 抢占。

下一步可继续 A 的真实合流准备：统一 Backend 传感器输出与 episode 目录、补运行资源版本元数据，并使配置切换 Mock/AirSim 不触碰 Runner 核心。正式比较实验在 P3 真实任务闭环、F11 seed/reset/统计规则冻结后再验收。冻结基线缺少 6 个 `.pytest_cache` 文件的问题见 [P2 报告](P2实现验收报告.md)，本轮没有改写冻结材料或 manifest。
