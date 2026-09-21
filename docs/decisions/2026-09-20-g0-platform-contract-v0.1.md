# G0 platform contract v0.1

事项编号 / 标题：F01—F06，平台协议、数据、动作、生命周期与记录最小基线；F09 的协议环境入口。  
状态：已实现的默认基线；本地自动验证、严格 wheel 安装和主代理接口审查通过，未发生 B/C 或真人负责人签字。  
适用版本：`drone.platform.contract/v0.1`；底层 `drone.simulator.contract/v1`。  
冻结日期：2026-09-20。

## 分层和兼容策略（F01）

新增根级 `contracts/`，不移动或改名 `simulator_contract/`。平台包和底层包都由分发包显式安装；未来 Backend 可以从已安装的底层包映射状态，而不是依赖工作目录。平台的 `PlatformObservation` 是 Agent、Task 和 Evaluator 每一步消费的综合状态；底层 `simulator_contract.contracts.Observation` 仍是传感器元数据加可选二进制 payload。二者不得以未限定的 `Observation` 名称混用。

本版本的读取器拒绝未知字段和未知枚举，防止静默改变语义。新增可选字段需要新的兼容读取策略和测试；删除或改变既有字段必须升 schema 版本。本次只新增平台包，不迁移冻结原件、底层适配器或课程材料。

## 数据、坐标、时间和传感器（F02/F03）

全部距离、速度和持续时间使用 SI；世界位置与速度使用 NED，机体速度若以后加入则使用 FRD。`PositionNed` 从 Backend 记录的**世界原点**计算；`TaskSpec.home_position_ned` 是独立的起飞/home 世界坐标，Mock 默认可为零点但不要求如此。`altitude_reference=relative_to_home` 意味着任务配置中“高度”参数若出现，必须说明相对 home 的语义，不能把 home 与世界零点混为一谈。

`wall_time_ns` 只用于记录关联；`simulator_time_ns` 可缺省且只能用于采样时刻。Runner 的时间预算将使用单调时钟（P2），不得把二者相减。传感器二进制写入 episode 文件，由 `SensorReference.relative_path` 引用；绝不放入 JSON。绝对或盘符路径被拒绝；缺失传感器在 `missing_sensors` 中以原因记录。

## 动作与错误（F04）

v0.1 是带 `deadline_s` 的同步 `execute`。该字段是从 Backend 收到动作开始计算的**单动作最大执行秒数**，不是绝对时刻；未来 Runner 必须用单调时钟强制它。`accepted` 仅表示 Backend 接受请求，`completed` 表示该请求已有结束状态，`succeeded` 表示 Backend 的执行结论；它们都**不是**到达任务目标的证明。Task 必须根据动作后的 Observation 评估到达。当前只定义 `move_to`（世界 NED 目标）和 `hover`。未知动作在解析时拒绝；Backend 不支持的已知能力返回结构化 `not_supported`，参数/连接/执行错误同样使用 `ContractError`。

MockBackend 只离散地把 `move_to` 更新为目标位置，并支持 `hover`；不模拟动力学、飞行安全、传感器、RPC、真实到达时间或真实 AirSim 资源。脚本化失败仍生成一份动作后 Observation，但位置保持不变。

## 生命周期、终止和记录（F05/F06）

P2 Runner 必须按照 `reset → observe_before → agent.act → backend.execute → observe_after → task.update → evaluator → record → cleanup` 调度。`StepRecord` 强制动作 ID 对齐，且 observation 序号是 N 和 N+1，因此最后动作后状态不能遗漏。初始化、成功、任务失败、超时、Agent/Backend/Task/Evaluator 错误与取消各有 `TerminationReason`；一次 EpisodeResult 只保留一个原始终止原因。

结束时无论成功、超时或错误都必须保留 `final_observation`。`CleanupResult` 单独记录收尾是否尝试、成功或错误，绝不能覆盖原始终止原因。episode 文件将以相对 `record_path`、步骤、事件、配置快照和 schema 关联；完整 Recorder 属 P2，不在本次实现范围。

`Backend.cleanup()` 是显式安全收尾，`Backend.close()` 是幂等的连接/资源释放；两者不能隐式互相替代。读取型 `observe()` 在无会话时抛出携带 `ContractError(code="not_connected")` 的 `PlatformContractException`，命令型 `execute()` 返回同一结构化错误的 `ExecutionResult`。本版 Evaluator 正式采用纯 `evaluate(steps, progress)`，不提供增量 `reset/update`；P2 Runner/Recorder 在 episode 结束时将完整步骤序列交给它。

## 实现与证据

定义：`contracts/model.py`、`contracts/interfaces.py`。最小消费者链路：`backends/mock/`、`agents/mock.py`、`tasks/mock.py`、`evaluators/mock.py`。过程 fixtures：`fixtures/episodes/`。边界测试：`tests/test_platform_contracts.py`、`tests/test_mock_chain.py`。

自动验证、主代理审查和真人 track 确认必须在 [P0-P1 验收报告](../validation/P0-P1验收报告.md) 分别登记，不能互相替代。真实仿真能力、ReachPoint 安全规则、搜索真值和批量实验仍分别是 F07/F08/F10/F11，未由本决策冻结。
