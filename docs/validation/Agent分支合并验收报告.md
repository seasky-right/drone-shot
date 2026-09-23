# Agent 分支本地合并验收报告

日期：2026-09-23。远端分支：`origin/agent`，提交 `9563c4d`（“初步完成agent task部分”）。本地集成分支：`codex/integrate-agent`。该提交新增 Agent、ReachPoint Task、样例配置与测试；没有 AirSim Backend 改动，因此按代码实际范围记录为 Agent/Task 合并。

## 合并范围

- 从远端主线创建本地集成分支并快进合入 `origin/agent`。原先尚未提交的 P2 与 Track A 工作保留在工作区，随后接入新 Agent 的 Mock CLI 入口。
- `drone-run --agent direct` 和 `--agent fixed-route` 分别消费分支中的 `DirectPointAgent` 与 `FixedRouteAgent`。使用分支的两个 ReachPoint 样例配置，经现有 EpisodeRunner、MockBackend、ReachPointEvaluator 和 Recorder 跑出成功结果与可重读记录。
- 分支的 `tasks/reach_point.py` 已单独通过 EpisodeRunner/MockBackend 集成测试。仓库此前已有功能更完整的 `tasks/reachpoint.py`，当前 CLI 继续使用它；两种实现的容差校验及可选到达规则尚未归一，后续须在 F08 冻结规则后收敛为单一公开入口。
- `LawnmowerSearchAgent` 已合入并保留其原有测试。它只负责搜索航线导航；真实 SearchTarget Task、传感器输入、真值和 Evaluator 仍属 P4。

## 验证与界限

- 分支合入后平台测试 `110/110`、底层适配测试 `9/9` 通过；新增集成测试覆盖新 Agent 的 CLI/Recorder 路径、分支 ReachPointTask 的 Runner 路径。依赖锁校验和 wheel 范围检查通过；独立安装 wheel 后，`direct` 与 `fixed-route` 两个命令入口均运行成功，新增模块从 `site-packages` 导入。
- 合流仍是无仿真 Mock 验证。没有启动真实 AirSim；P3 真实 ReachPoint 与 P4 SearchTarget 不能据此标为已验收。
- 未改写冻结课程材料、`airsim-settings/drone.json` 或基线 manifest；未推送本地集成分支。远端分支可继续由原作者独立开发。
