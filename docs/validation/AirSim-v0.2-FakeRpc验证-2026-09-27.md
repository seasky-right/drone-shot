# AirSim v0.2 FakeRpc 验证（2026-09-27）

新增 `drone.v02.airsim/backend`、固定场景、单机 ReachPoint Task 和逐机 Agent。旧 `drone.airsim/backend` v0.1 ID 与 CLI 保留。v0.2 Backend 复用旧 `AirSimBackend` 的起飞、控制、状态读取和安全收尾；图像由旧适配获取后经 Core Artifact Store 写入 episode。

示例 `builtin_pack/sample-airsim-v02.json` 采用已记录成功的 `configs/airsim-p3-success.local.json` 中的 `Drone`/默认 RPC 载具、出生点 `(4, 0, -2)`、home `(4, 0, -0.24)`、目标 `(4, 0.5, -2)` 及控制时限。历史配置未修改。FakeRpc 模拟旧场景出生点接触地面后的高度变化，不等于再次运行该 UE4 场景。

无仿真验证命令：

```powershell
python -m pytest tests/test_airsim_v02_plugin.py -q
python -m core.plugin_cli preflight builtin_pack/sample-airsim-v02.json --manifest builtin_pack/drone_plugin.json --enable-backend
python -m pytest tests -q
```

FakeRpc 用例覆盖 v0.2 `run_multi` 成功记录与结果重读、RGB/Depth 文件、动作失败后降落、相机缺失导致 reset 失败并释放连接、显式启用及单机容量预检。真实 UE4/AirSim 尚未运行本 v0.2 路径。`capabilities()` 在连接前不声称具体相机已确认，因此 `required_sensor_resources` 的 Core 预检会拒绝；reset 实际采集并拒绝缺失相机。固定场景由用户确认已加载，插件不校验 UE4 场景内容或提供真实双机能力。

本机结果：定向 FakeRpc 与 Pack 探针 8 项通过；`python -m pytest tests -q` 为 276 passed、2 skipped。`python scripts/build_builtin_pack.py --output <临时目录>` 成功构建 wheel，安装到临时目录后自动发现 `drone.v02.airsim/backend` 并通过示例组合预检；wheel 内含 `airsim_v02.py`、Manifest、`sample-airsim-v02.json`。通用 conformance 中新增的场景、任务和逐机 Agent 通过无仿真探针；AirSim Backend 未用通用探针启动真实仿真，标为 unchecked，其 FakeRpc 行为由专门集成测试覆盖。

独立 Pack 安装复查命令（`<临时目录>` 为本机可写临时路径）：

```powershell
python scripts/build_builtin_pack.py --output <临时目录>/wheels
python -m pip install --no-deps --target <临时目录>/installed <临时目录>/wheels/drone_builtin_pack-0.1.0-py3-none-any.whl
```

更新示例后重构建的 wheel SHA-256 为 `053bd6b2a394b101205ab89b717ba7e920562b7d618d0318d3ff5e33645f4323`；wheel 内示例与工作区文件字节一致。将 wheel 安装至 `C:/Users/seasky/AppData/Local/Temp/drone-airsim-v02-installed-final` 并放在 `sys.path` 首位后，`builtin_pack.airsim_v02.__file__` 位于该安装目录。`PluginRegistry.discover()` 无 issue，`_preflight(registry, sample, enable_backend=True)` 返回 `backend`、`scenario`、`task`、`agent:pilot`。
