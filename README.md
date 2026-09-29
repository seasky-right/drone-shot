# Drone spatial-intelligence platform

This repository is preparing a simulator-independent platform for drone tasks.
The current Plugin Contract v0.2 candidate has a Core/Contracts wheel, a separate first-party component Pack, static plugin discovery/preflight, and a two-vehicle Mock execution path. The earlier single-vehicle ReachPoint and AirSim routes remain available through explicit adapters. One real UE4 AirSim ReachPoint success and one controlled failure have been recorded and replayed; P3 formal acceptance and a real-simulator benchmark remain later work. See the [plugin developer guide](docs/plugins/README.md) for the new API and its limits.

Read [the current route](综述.md) first, then the [planning index](docs/planning/README.md) and [current status report](docs/validation/项目现状报告-2026-09-25.md). The former old overview is archived under `docs/archive/` and is not a delivery baseline.

## Install and test without a simulator

Python 3.13 is required; P0 was validated with CPython 3.13.5. Core now depends on `packaging` and `jsonschema` for version and configuration checks. Mock use does not require Unreal, AirSim, course resources, or `.driver-cache`.

```text
python -m venv .venv
.venv\\Scripts\\python -m pip install --upgrade pip
.venv\\Scripts\\python scripts/build_core_wheel.py --output dist
.venv\\Scripts\\python scripts/build_builtin_pack.py --output dist
.venv\\Scripts\\python -m pip install dist/drone_platform-0.1.0-py3-none-any.whl dist/drone_builtin_pack-0.1.0-py3-none-any.whl
.venv\\Scripts\\python -B -m unittest discover -s tests -v
.venv\\Scripts\\python -B -m unittest discover -s simulator_contract/tests -v
```

`requirements-dev.lock` records the absence of separate test-only dependencies. `requirements-build.lock` mirrors the PEP 517 build requirement in `pyproject.toml`; verify it with `python -B scripts/verify_dependency_lock.py`. The first-party Pack carries the preserved lower-level AirSim adapter and its `msgpack` dependency.

`config.example.json` is a checked-in `BackendConfig` plus `TaskSpec` example for the Mock backend. Use a `config.local.json` or `configs/<name>.local.json` file for machine-specific settings; those paths are ignored.

Run one no-simulator ReachPoint episode with `drone-run --config config.example.json --agent fixed --output runs` after installation, or `python -B run.py --config config.example.json --agent fixed --output runs` from the repository. Results include a task/backend snapshot, trajectory, events, and `result.json`. `--agent hover` exercises an alternative Agent. The CLI also accepts `--agent direct` with `configs/reach_point.example.json` and `--agent fixed-route` with `configs/reach_point_fixed_route.example.json`, using the merged Agent branch implementations. The pushed `LawnmowerSearchAgent` is a navigation component; a SearchTarget Task/Evaluator and real search benchmark remain P4 work. The same ReachPoint configuration can select AirSim by setting `backend_config.backend_type` to `airsim` and supplying the local simulator connection values. Set scene-specific `task_spec.parameters.action_deadline_s`, waypoint, speed and tolerance values before flight, then use `python -B run.py --config configs/airsim.local.json --agent fixed-route --output runs --enable-airsim` with a prepared simulator. The explicit switch authorizes this run to connect, take off, move, return home, and land. The existing Mock command needs no switch. Independent real AirSim smoke checks use `python -B -m backends.airsim.smoke`; full simulator validation remains pending.

## 简明终端界面

从源码目录打开 PowerShell，使用本机已安装依赖的 Python 3.13，可按下面的顺序体验 v0.2 Mock（无需启动模拟器）：

```powershell
D:\Anaconda\python.exe -B -m core.console catalog
D:\Anaconda\python.exe -B -m core.console select --config builtin_pack/sample-v02.json --output runs
D:\Anaconda\python.exe -B -m core.console result builtin-v02-two-vehicle --output runs
```

`select` 会显示车辆、绑定、动作、当前组件和组合状态，不会每轮打印整份 JSON。`builtin_pack/sample-v02.json` 已选好 Mock 后端、固定场景、任务和智能体；状态应为“组合完整/预检通过，可无模拟器运行”。组件菜单会隐藏当前组合中可静态判定不兼容的选项；尚需填写配置的组件仍可选择，提交前会再次检查。结果处理器须先选择基准测试。进入菜单后输入 `p` 可只预检且不生成运行记录，输入 `r` 会先预检再运行，以状态行显示进度并在结束时给出结果、步数或用例数和记录路径。输入编号选择组件，输入 `0` 可取消菜单中标明可取消的选项，直接回车保留当前选择；输入 `s` 将完整配置保存为新 JSON 文件，输入 `q` 退出。场景和场景生成器是二选一来源，另一项显示“未使用”；运行环境、评价器和基准测试等可选项未启用不影响普通单次运行。

可选的 Mock 基准测试使用 `D:\Anaconda\python.exe -B -m core.console select --config builtin_pack/sample-benchmark-v02.json --output runs`，进入菜单后输入 `r`；随后用 `D:\Anaconda\python.exe -B -m core.console result builtin-v02-benchmark --benchmark --output runs` 回读汇总。AirSim 固定场景的起点是 `builtin_pack/sample-airsim-v02.json`：先审查其中的坐标、车辆与连接参数，再运行 `D:\Anaconda\python.exe -B -m core.console select --config <本地配置.json> --output runs` 并输入 `p` 检查静态组合。状态“组合兼容”只说明组件配置与能力匹配；实际运行还需已启动对应 UE4 场景，并在 `select` 命令中显式添加 `--enable-airsim` 后输入 `r`。内置 AirSim 没有场景生成器；与 AirSim 不兼容的 Mock 生成器不会出现在可选列表中。

AirSim 显式目标 ReachPoint 的单次配置、固定场景重复 Benchmark、指标含义及真实试飞边界见 [AirSim ReachPoint v0.2](builtin_pack/README-airsim-reach.md)。

`drone-console` 可查看已实现功能和最近的运行结果。执行 `drone-console run --config config.example.json --output runs` 可运行一次 Mock ReachPoint，并实时查看连接、动作、位置、收尾、指标和记录路径。用 `drone-console status --output runs` 浏览最近记录，用 `drone-console show --output runs` 查看最近一次完整记录；`show <episode-directory>` 可指定某次记录。在本机源码目录中，可将命令开头的 `drone-console` 换成 `D:\Anaconda\python.exe -B -m core.console`。

运行真实场景时，先准备本地 AirSim 配置，再执行 `drone-console run --config <local-config> --agent fixed-route --enable-airsim`。终端只连接**已启动**的模拟器，不会启动模拟器或打开窗口，也不会隐藏已显示的窗口。场景安全参数和启动步骤请遵照 [P3 真实飞行记录](docs/validation/P3-AirSim-真实飞行验证报告.md)。

查看 v0.2 插件时，`drone-console catalog` 会列出发现的所有组件类型，并标明旧版组件和仅供目录查看的组件。在交互式终端中执行 `drone-console select --config builtin_pack/sample-v02.json --output runs`，可从可运行的双机 Mock 组合开始选择。按角色选择组件并查看简明状态；输入 `v` 可开启本次单次任务的本地实时页面（默认关闭），页面展示公开状态、位置轨迹和最近一次 RGB 帧；Mock 轨迹标为示意，AirSim 图片按观测采集更新，并非连续视频。运行后按回车关闭页面。输入 `s` 将完整配置保存到新 JSON 路径，输入 `r` 预检查并运行。保存后的配置可能还需修改才能通过预检查。保存文件包含配置中的密钥。基准测试暂不提供实时页面。`builtin_pack/sample-benchmark-v02.json` 是带结果处理器的 Mock 基准测试示例；安装 wheel 的用户可先将示例复制到本地。运行 v0.2 AirSim 时，应以 `builtin_pack/sample-airsim-v02.json` 为基础审查并准备本地配置，再添加 `--enable-airsim`。选中 AirSim 不会回退到 Mock，真实场景必须事先启动。

用 `drone-console result <episode-id> --output runs` 读取 v0.2 单次运行摘要；基准测试结果使用 `drone-console result <benchmark-id> --benchmark --output runs`。需要完整机器可读结果时添加 `--json`。旧版 `status` 和 `show` 读取 v0.1 Recorder 记录。非交互式 v0.2 脚本可用同一份 JSON 配置执行 `drone-plugins preflight`、`run-multi` 和 `run-benchmark`。

## Track A Mock experiments

`drone-experiment --config experiment.example.json --output experiments` runs the same Mock task with three Agent configurations across 20 seed values. The generic `ExperimentManager` records every attempted episode, aggregates success and numeric metrics, and writes `config.json`, `summary.json`, `metrics.csv` and `report.md`. The CLI resolves Agent/Task/Evaluator factories from installed Pack metadata; the platform manager itself depends only on the shared interfaces. `drone-replay <episode-directory>` rereads and validates saved trajectory, events, result and sensor references without sending flight commands.

For a batch using the merged ReachPoint Task/Evaluator and three Agent implementations, run `drone-experiment --config reachpoint.experiment.example.json --output experiments/reachpoint-local`. This is a Mock route and recorder comparison; see [the ReachPoint batch report](docs/validation/Track-A-ReachPoint-Mock批量实验报告.md) for the 60-episode result and limits.

MockBackend has no scene randomization. The seed is passed through `TaskSpec`; repeated seed values are comparable inputs, not evidence of independent random scenes. Real benchmarking still requires P3 validation and F11 comparison rules.

Build Core from clean staging with `python scripts/build_core_wheel.py`, then run
`python scripts/verify_wheel.py <wheel-path>`. This checks that the Core wheel
contains only `core/`, `contracts/` and distribution metadata. The first-party
components are built by `scripts/build_builtin_pack.py`.

## Boundaries and real simulator work

`contracts/` is the platform-layer API. `simulator_contract/` remains the independent lower-level adapter boundary; `backends/airsim/` maps between them without exposing AirSim RPC to the platform core. The two Observation types are intentionally distinct: `PlatformObservation` is the per-step state handed to agents/tasks, while `simulator_contract.contracts.Observation` is metadata plus optional sensor binary payload.

The UE4/AirSim course material and `airsim-settings/drone.json` are frozen local resources. If present, check their historic checksum with `python -B simulator_contract/freeze_baseline.py --verify`. The current manifest check reports six missing `.pytest_cache` files in the frozen course directory; all remaining 1,383 entries match. This and no-simulator tests do not prove a real flight.

## Source and resource scope

The Core and first-party Pack are separate wheels. Large scenes, drivers, generated runs, and course resources are local references, not ordinary-source uploads. See [the upload checklist](docs/planning/GitHub上传前仓库整理清单.md) and [resource manifest](assets/manifests/legacy-ue4-airsim.md).

The private collaboration baseline is authorized for `seasky-right/drone-shot`; collaborators remain to be specified. Project licence and external-resource distribution rights remain unresolved. This repository does not publish external resources.

## Offline ReachPoint evaluation

The local evaluator scores versioned episode JSON records and compares runs made under the same conditions without requiring a simulator. It writes `result.json`, `summary.json`, and a Markdown comparison report. See the [local evaluation guide](docs/evaluation/本地评价使用说明.md). The included trajectories are synthetic fixtures rather than AirSim flight results.

Validate the versioned ReachPoint pack with `python -B scripts/validate_reach_point_pack.py`. The pack defines one case, three baselines, and five repeats. An adapter from this pack manifest to the current `ExperimentManager`, plus trusted elapsed-time and event recording, is still required before the planned 15 benchmark episodes can be treated as an official result.
