# P3 AirSim 本地接入修复验收报告

日期：2026-09-24。范围：Track B 两处缺陷修复、AirSimBackend 与单次 ReachPoint CLI/Runner/Recorder 的无仿真接入。此报告不构成真实 AirSim 飞行或 P3 正式验收。

## 变更与可复查证据

| 项目 | 当前行为 | 验证 |
| --- | --- | --- |
| 悬停确认 | 用 NED 三轴速度范数与配置容差比较；垂直运动超限不再误判成功。 | FakeRpc 在垂直速度 2 m/s 时返回超时，归零后成功。 |
| 传感器文件 | 每次采样生成独立路径并独占写入；同一观察序号或归一化后相同的传感器 ID 不再覆盖旧图像。 | 四帧内容及路径独立；完整 episode 的动作前后图像可重读。 |
| Episode 资源根目录 | Runner 对所有 Backend 将有效 `resource_root` 设为本次 episode 目录，并记录在 `backend.json`。 | Mock 配置注入测试和 AirSim FakeRpc CLI 完整任务验证。 |
| CLI 切换 | 同一 ReachPoint 配置可选 Mock 或 AirSim；AirSim 需显式 `--enable-airsim`。任务成功但收尾失败时 CLI 返回非零，`result.json` 保留原始任务结果。 | CLI 选择、门槛、收尾失败与完整任务测试。 |

## 本地验证

- `python -m pytest tests simulator_contract/tests -q`：144 项通过。
- `python -B scripts/verify_dependency_lock.py`：构建依赖锁一致。
- `python -m pip wheel . --no-deps --no-build-isolation --wheel-dir dist`：wheel 构建成功；`scripts/verify_wheel.py`：包范围检查通过。
- 在独立虚拟环境安装 wheel，并从仓库外确认 `core` 与 `backends.airsim` 均从 `site-packages` 加载；安装后的 `drone-run` 完成 Mock ReachPoint episode，`drone-replay` 可重读结果。
- AirSimBackend 经 FakeRpc 由公开 CLI 完成成功和可控移动失败两类 episode，覆盖 reset、起飞、航点、观测、返航、降落、记录及重读；不依赖真实仿真器。

## 尚需现场验收

F07 真实能力矩阵与独立 smoke 尚无新实测；F08 的真实航点、速度、动作期限、容差、图像资源、兼容性降落判据仍需在运行前冻结。真实成功与可控失败 episode、场景及客户端版本记录仍属 P3 验收。当前测试不能证明真实飞行、传感器可用性或物理触地。
