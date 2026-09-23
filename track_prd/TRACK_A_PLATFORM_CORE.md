# Track A — Platform Core

> 适用说明（2026-09-20）：本 PRD 保留职责与目标描述；与当前实现不符时，以仓库代码和验证记录为准，不将计划视为已实现。整体路线以 [综述.md](../综述.md) 为基准，近期执行以 [近期开发执行计划](../docs/planning/近期开发执行计划.md) 为准。当前 `simulator_contract/` 是底层仿真协议，`contracts/` 平台 v0.1 协议已实现；两层通过 Backend 适配，真实闭环仍待验收。近期计划将首次真实合流调整为 ReachPoint，并由 C 在该合流前提供最小 Evaluator；原文 SearchTarget 合流作为后续目标。

> 进度快照（2026-09-23）：V0.1 公共协议与 Mock、V0.2 Mock episode、V0.3 Mock 批量实验（3 Agent × 1 Task × 20 seeds）已有本地验收。真实 AirSim episode 与正式 Benchmark 尚未验收；以 [Track A V0.3 Mock 报告](../docs/validation/Track-A-V0.3-Mock验收报告.md) 及 [近期开发执行计划](../docs/planning/近期开发执行计划.md) 为准。

> 负责人：主线开发  
> 目标：实现与具体仿真器、具体任务解耦的平台运行内核，并负责后续三条开发线的总体集成。

## 1. 责任边界

本 Track 负责：

- `contracts/` 的维护与版本冻结；
- `MockBackend`、`DummyTask`、`DummyAgent`、`DummyEvaluator` 等测试替身；
- `EpisodeRunner`；
- `Recorder`；
- `EpisodeResult`；
- `ExperimentManager`；
- 通用统计、结果汇总与报告输出；
- CLI 入口与整体集成测试。

本 Track 不负责：

- AirSim / Isaac 等具体仿真器 API；
- 具体搜索、测绘、定位任务的业务逻辑；
- 具体任务指标的定义；
- RL / VLM / 路径规划算法本身。

原则：**即使其他两个 Track 暂时不可用，本 Track 也必须能依靠 Mock 独立运行。**

---

## 2. 公共 Contracts v0.1

三条 Track 在正式分开前共同冻结最小协议：

### 数据结构

- `TaskSpec`
- `Observation`
- `Action`
- `EpisodeEvent`
- `EpisodeResult`

### 接口

- `Agent`
- `SimulatorBackend`
- `Task`
- `Evaluator`

接口第一版保持最小化，后续新增字段应优先采用向后兼容方式。

示意：

```python
class SimulatorBackend:
    def reset(self, task_spec): ...
    def observe(self) -> Observation: ...
    def execute(self, action: Action): ...
    def close(self): ...

class Agent:
    def reset(self, task_spec): ...
    def act(self, observation: Observation) -> Action: ...

class Task:
    def reset(self, task_spec): ...
    def update(self, observation, action, event): ...
    def is_done(self) -> bool: ...

class Evaluator:
    def reset(self, task_spec): ...
    def update(self, observation, action, event): ...
    def result(self) -> dict: ...
```

---

# 3. V0.1 — Core / Mock 基础

## 开发内容

### 3.1 建立核心目录

```text
contracts/
core/
backends/mock/
agents/mock/
tasks/mock/
tests/
```

### 3.2 实现 MockBackend

MockBackend 不追求物理真实性，只用于隔离真实仿真器依赖。

至少支持：

- `reset`
- `observe`
- `execute`
- `close`

基础 Action：

- takeoff
- land
- hover
- move_to
- move_velocity
- capture
- return_home

### 3.3 建立 Dummy 组件

实现：

- `DummyAgent`
- `DummyTask`
- `DummyEvaluator`

用于验证公共 Contracts 是否足够支撑完整调用链。

### 3.4 Contract Tests

验证：

- Action 可以被 Backend 消费；
- Observation 可以被 Agent 消费；
- Task / Evaluator 能够接收统一过程数据；
- MockBackend 与未来真实 Backend 可替换。

## V0.1 本 Track 验收

```text
MockBackend
+
DummyAgent
+
DummyTask
+
DummyEvaluator
```

可以在不启动 UE / AirSim 的情况下完成基本调用测试。

---

# 4. V0.2 — Episode Runtime

## 开发内容

### 4.1 EpisodeRunner

建立标准 episode 生命周期：

```text
backend.reset()
task.reset()
agent.reset()
evaluator.reset()

while not done:
    observation = backend.observe()
    action = agent.act(observation)
    event = backend.execute(action)

    task.update(...)
    evaluator.update(...)
    recorder.record(...)

输出 EpisodeResult
```

统一处理：

- success
- failure
- timeout
- backend error
- agent error
- task error

### 4.2 Recorder v0.1

保存：

- timestamp
- observation 摘要
- action
- position / orientation
- task event
- error
- 关键 RGB / Depth

建议输出：

```text
runs/<episode_id>/
├─ task.yaml
├─ result.json
├─ trajectory.jsonl
├─ events.jsonl
└─ images/
```

### 4.3 CLI v0.1

目标调用形式：

```bash
python run.py --task tasks/demo.yaml --agent agents/demo.py
```

平台负责自动构建 Runner 并运行一个 episode。

## V0.2 本 Track 验收

使用：

```text
MockBackend
+ DummyTask
+ DummyAgent
+ DummyEvaluator
```

完整输出：

```text
EpisodeResult
trajectory
events
logs
```

此时不依赖 AirSim，也不依赖真实 Task。

---

# 5. V0.3 — Experiment / Benchmark Infrastructure

## 开发内容

### 5.1 ExperimentManager

支持：

```text
Task
× Agent
× Seed List
× Repeat N
```

自动运行多次 EpisodeRunner。

### 5.2 通用统计

平台只做与具体任务无关的统计，不定义业务指标。

例如 Evaluator 返回：

```json
{
  "success": true,
  "time": 75.2,
  "localization_error": 2.3
}
```

Core 负责聚合：

- success rate
- mean
- median
- std
- min / max
- episode count

### 5.3 Experiment Result

建议：

```text
experiments/<experiment_id>/
├─ config.yaml
├─ episodes/
├─ summary.json
├─ metrics.csv
└─ report.md
```

### 5.4 对比运行

支持多个 Agent 使用：

- 相同 Task；
- 相同 seeds；
- 相同 Backend 配置；

输出横向对比结果。

## V0.3 本 Track 验收

即使仍使用 Mock，也可以执行：

```text
3 Agents
× 1 Task
× 20 seeds
```

并自动输出统一 Benchmark 报告。

---

# 6. 与其他 Track 的依赖方式

## 对 Track B

只依赖：

```text
SimulatorBackend
```

Core 不允许 import `backends/airsim/` 内部实现。

## 对 Track C

只依赖：

```text
Task
Evaluator
Agent
TaskSpec
```

Core 不允许硬编码 Search / Survey 等任务规则。

---

# 7. 合流节点

## Integration 0 — Contracts Freeze

三人共同确认：

- 字段命名；
- 坐标约定；
- Observation / Action 类型；
- error 类型；
- version。

冻结后各自独立开发。

## Integration 1 — V0.2 Real Pipeline

第一次真实系统合流：

```text
AirSimBackend
+
EpisodeRunner
+
SearchTargetTask
+
RuleBasedAgent
+
SearchEvaluator
```

目标：真实运行一个完整 episode。

## Integration 2 — V0.3 MVP

正式形成：

```text
ExperimentManager
+
真实 AirSimBackend
+
真实 Task / Evaluator
+
多个 baseline Agent
```

输出可展示的 Benchmark 结果。

---

# 8. 后续责任延续

V0.4 以后，本 Track 继续负责：

- Experiment Manager 扩展；
- batch / queue；
- Runner 调度；
- training wrapper；
- multi-agent runtime；
- service worker；
- Web / CLI 后端；
- 第三方 Agent 生命周期管理。

本 Track 始终是平台主线。
