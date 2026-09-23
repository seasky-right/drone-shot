# ReachPoint 本地评价链路验收记录

日期：2026-09-23。范围：FR-EVL-02 时间评分、FR-EVL-03 综合评分、FR-EVL-04 结果输出与比较；同时复验 FR-EVL-01 及 Track C baseline/Benchmark Pack。

## 验证结果

- 生成四份合成过程记录，使用 `evaluate-batch` 输出四个 `result.json`，使用 `summarize` 输出 `summary.json` 与 `report.md`。
- 固定样例中，4 秒到达得 88 分；两组合成轨迹的平均分分别为 86.5 和 74.5。报告含成功率、平均分、标准差、完成时间中位数、路径中位数、碰撞率、超时率和终止分类。
- 合入最新 `main` 后，`python -B -m unittest discover -s tests -v`：125 项通过；底层 `simulator_contract` 9 项另行复验通过。
- 测试涵盖输出文件、同条件比较、缺少重复拒绝比较、基础设施错误单列并暂停排名、负时间和虚假成功拒绝评价。
- `python -B scripts/validate_reach_point_pack.py` 通过：1 个案例、5 个 seed、3 个 baseline，共规划 15 次运行；仅验证配置与实现可解析，未声称已经飞行。

本机 bundled Python 为 3.12.14，尚未在仓库声明的 Python 3.13 环境复验。当前闭环以合成记录为输入；通用 Runner/Recorder 已合入，但丰富评价记录适配与真实 AirSim 飞行证据仍未完成。使用方法见 [本地评价使用说明](../evaluation/本地评价使用说明.md)。
