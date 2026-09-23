# ReachPoint C1 本地验收记录

日期：2026-09-22。依据：《无人机智能体评价系统与 Benchmark 构建规划书》C1，以及仓库《近期开发执行计划》的 C 工作包。

## 本次完成

- 独立 `ReachPointTask`：任务参数校验、动作后位置到达判定及 Backend 失败分类。
- 独立 `ReachPointEvaluator`：兼容 G0 `evaluate(steps, progress)` 的 FR-EVL-01 指标入口；增加 C1 完整计算入口 `evaluate_episode`，读取单调 elapsed、事件与 cleanup，给出可复算的原始指标和成绩。
- 八类固定样例：成功、容差边界、未到达、超时、Backend 失败、碰撞、返航失败、降落失败。另有时间超预算、事件不可用、两种碰撞政策及安全收尾的测试。
- 规则说明见 `fixtures/reach_point/README.md`。该规则的样例数值不作为真实 AirSim 场景验收参数。

## 验证

在当前克隆仓库根目录运行 bundled Python 3.12.14：

```text
python -B -m unittest discover -s tests -v
```

结果：21 项通过。此前 P1 为 15 项，本次新增 6 项。`git diff --check` 通过。测试全部无仿真；本机未发现 Python 3.13 解释器，因此尚未在仓库声明的 Python 3.13 环境复验。

## 合流状态更新（2026-09-23）

最新 `main` 的 EpisodeRunner、Recorder 与 ExperimentManager 已合入本分支，并与评价代码共同通过测试。离线评价器使用的丰富 episode 记录尚未由 Recorder 直接生成：仍需适配 `elapsed_monotonic_s`、完整事件来源及 cleanup 字段后，才能把运行结果交给 `evaluate_episode`。完整安全事件只有在 Runtime 证实采集能力后才能传空列表。真实 AirSim 成功/失败样例属于 C4，不能由本次 Mock/fixture 验证替代。
