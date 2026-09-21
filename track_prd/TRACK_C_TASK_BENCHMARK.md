# Track C — Task & Benchmark Pack

> 适用说明（2026-09-20）：本 PRD 保留职责与目标描述；与当前实现不符时，以仓库代码和验证记录为准。整体路线以 [综述.md](../综述.md) 为基准，近期执行以 [近期开发执行计划](../docs/planning/近期开发执行计划.md) 为准。近期计划先以 ReachPoint 真实合流，将最小 Evaluator 提前至 V0.2 合流前交付；SearchTarget 随后推进。本文 `target_detected`、`target_position` 仅为示意，检测来源、目标上报和评测真值隔离尚未冻结；seed 列表不代表场景随机化已实现。

> 负责人：任务、评测与 baseline 开发  
> 目标：定义平台到底测试什么、怎么算完成、怎么算表现，并提供第一批可复现 Benchmark。

## 1. 责任边界

本 Track 负责：

- TaskSpec 示例；
- Task 实现；
- Evaluator；
- baseline Agent；
- Benchmark Pack；
- 测试 fixtures；
- 任务指标定义；
- 后续遥感 / GIS 任务体系。

本 Track 不负责：

- AirSim API；
- EpisodeRunner；
- Experiment Manager；
- 仿真进程管理；
- Web。

原则：**任务与评测必须可以只依赖统一 Observation / Action / EpisodeEvent 开发，不直接访问 AirSim。**

---

# 2. 独立开发方式

本 Track 使用：

```text
fake Observation
fake trajectory
recorded episode
```

开发。

例如：

```python
Observation(
    position=(10, 5, 3),
    target_detected=True,
    target_position=(20, 15, 0)
)
```

因此即使 UE / AirSim 不可用，也可以完成绝大部分 Task / Evaluator 测试。

---

# 3. V0.1 — Task Schema / Fixtures

## 3.1 第一批任务

第一版只做最小任务，不扩大范围。

建议：

### ReachPoint

目标：

```text
从初始位置到达指定空间位置。
```

用途：

- 验证飞行；
- 验证 Task 生命周期；
- 验证位置指标。

### SearchTarget

目标：

```text
在指定区域寻找目标并报告目标位置。
```

用途：

- 第一版可展示主任务；
- 后续接目标识别、定位、VLM。

### SimpleSurvey

目标：

```text
覆盖指定区域并完成基础观测。
```

用途：

- 为后续遥感 / GIS / coverage 打基础。

## 3.2 TaskSpec 示例

例如：

```yaml
name: search_target
scene: modular_neighborhood
seed: 101

vehicle:
  start_position: [0, 0, 0]

goal:
  type: find_target
  target: vehicle

limits:
  timeout: 180
```

## 3.3 Fixtures

建立：

```text
tests/fixtures/
├─ observations/
├─ trajectories/
├─ events/
└─ episodes/
```

包含：

- 成功轨迹；
- timeout；
- 未找到目标；
- 定位误差；
- 碰撞；
- 成功返航；
- 未返航。

## V0.1 本 Track 验收

TaskSpec 可以被解析；

Task / Evaluator 的后续开发不需要真实 Simulator。

---

# 4. V0.2 — Task + Baseline Agent

## 4.1 Task Runtime

实现至少：

```text
ReachPointTask
SearchTargetTask
```

Task 负责：

- 初始化；
- 维护任务状态；
- 判断 done；
- 判断基础 success / failure；
- timeout；
- 任务事件。

Task 不负责通用运行循环。

## 4.2 Baseline Agents

至少准备：

### RandomAgent

用于验证最弱基线与异常路径。

### FixedRouteAgent

固定航线执行。

用于：

- 验证 Task；
- 验证 Runner；
- 验证路径长度 / 时间记录。

### RuleBasedAgent

基于 Observation 做简单条件判断。

用于：

- 第一可展示版本；
- 与后续 RL / VLM 对比。

所有 Agent 均只遵循：

```text
Observation → Action
```

严禁直接 import AirSim。

## V0.2 本 Track 验收

使用 fake Observation / MockBackend 条件下：

```text
Task
+
Agent
```

能够完整完成成功、失败和 timeout 流程。

---

# 5. V0.3 — Evaluator / Benchmark Pack

## 5.1 Evaluator 独立

从 Task 中拆出正式 Evaluator。

原则：

```text
Task = 要做什么、什么时候结束
Evaluator = 做得怎么样
```

### SearchTargetEvaluator

候选指标：

- success；
- target_detected；
- localization_error；
- completion_time；
- path_length；
- collision_count；
- safe_return。

### ReachPointEvaluator

候选指标：

- success；
- final_position_error；
- completion_time；
- path_length；
- collision_count。

### SimpleSurveyEvaluator

第一版可先提供：

- coverage；
- completion_time；
- path_length；
- safe_return。

更复杂的遥感质量指标留给 V0.5。

## 5.2 Benchmark Pack v0.1

冻结：

```text
任务：
- ReachPoint
- SearchTarget

Agents：
- Random
- FixedRoute
- RuleBased

Seeds：
- 固定 seed 集
```

例如：

```text
101
102
103
...
120
```

确保多个 Agent 使用完全相同测试条件。

## 5.3 Benchmark Config

建议：

```yaml
benchmark: search_v0.1

task: search_target

seeds:
  - 101
  - 102
  - 103

agents:
  - random
  - fixed_route
  - rule_based

metrics:
  - success
  - completion_time
  - path_length
  - localization_error
```

## 5.4 Evaluator Unit Tests

直接对 fixtures 断言，例如：

```text
已发现目标 + 坐标误差 2m
→ localization_error = 2

发生碰撞
→ collision_count += 1

任务完成且返航
→ safe_return = true
```

## V0.3 本 Track 验收

仅使用 fixtures 即可稳定产生任务指标。

Benchmark Pack 可以交给 Core 的 ExperimentManager 自动批量运行。

---

# 6. 与其他 Track 的依赖

## 对 Track A

只依赖：

```text
TaskSpec
Observation
Action
EpisodeEvent
Task
Evaluator
Agent
```

不知道 EpisodeRunner 如何实现。

## 对 Track B

原则上零直接依赖。

需要新的传感器 / 状态时：

```text
提出 Observation Contract 变更
```

而不是：

```python
import airsim
```

---

# 7. 合流节点

## Integration 0

三人共同冻结：

- Observation 字段；
- Action 类型；
- TaskSpec 基础格式；
- 坐标约定。

## Integration 1

第一次真实合流使用：

```text
SearchTargetTask
+
RuleBasedAgent
+
SearchTargetEvaluator
```

接入真实 AirSimBackend + EpisodeRunner。

## Integration 2

V0.3 MVP：

```text
Benchmark Pack v0.1
```

跑多个 Agent × 多个 seeds，输出真实比较结果。

---

# 8. 后续责任延续

V0.4 以后，本 Track 继续负责：

- 更复杂 Benchmark；
- 遥感 / GIS 任务；
- 搜索定位；
- 测绘；
- 巡检；
- 空间指标；
- 场景随机化参数定义；
- synthetic data ground truth 定义；
- 多无人机协同任务与指标；
- failure slice；
- 自动课程 / 自动任务生成。

本 Track 始终回答两个问题：

> 平台到底让无人机做什么？

> 做得好不好，怎么算？
