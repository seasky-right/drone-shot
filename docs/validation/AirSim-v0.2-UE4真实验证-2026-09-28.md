# AirSim v0.2 UE4 真实验证（2026-09-28）

后续（2026-09-28）：本文记录的显式相机资源预检拒绝已修复；安装版 Core/Pack 在完整隔离场景副本上的 `rgb:0`、`depth:0` 实测成功，且冻结源运行前后清单一致。见[相机资源隔离复验](AirSim-v0.2-相机资源隔离复验-2026-09-28.md)。以下保留本次原始证据与当时限制。

本次以安装版 Core 和 builtin Pack 的公开 `core.plugin_cli run-multi --enable-backend`，在冻结 UE4.25.4 `ModularNeighborhood` 固定场景完成 v0.2 单机成功和短期限可控失败。原始记录位于 Git 忽略目录 `runs/real-airsim-v02-20260928/`，未提交。此证据只覆盖指定场景、单机 ReachPoint 技术流程，不是场景生成、SearchTarget、真实双机或 F07/F08 规则确认。

## 输入与过程

- 场景 EXE SHA-256：`1fd385b9e35e68bba21f18007b8d001d521c2c128d213634ab88587590ba151e`，与旧 P3 记录一致。Python 3.13.5；用户 `Documents/AirSim/settings.json` 在检查时不存在，未创建或修改设置。
- 当前工作区构建并安装的 Core wheel SHA-256：`1396f29f5b5eeab8f3f4d44bcbafcc16cdb7bf2dafc742bf365085d69e1f203e`；builtin Pack wheel：`bdfff658ae21660c76728593edbab4d183321ee4647d333f5cf6bd9acb296384`。`python -P` 检查显示 `core` 和 `builtin_pack` 均从新目录 `venv/Lib/site-packages` 导入；两份配置经安装版 `preflight --enable-backend` 通过。运行记录 `run.json` 的组件来源为 `distribution:drone-builtin-pack==0.1.0`。
- 成功配置为 `builtin_pack/sample-airsim-v02.json`，目标 NED (4, 0.5, -2)、容差 0.35 m、动作期限 25 s；可控失败配置保存在新证据目录 `controlled-failure.json`，仅使用独立 episode ID 和 0.05 s 动作期限。两次均调用安装版 `python -P -B -m core.plugin_cli run-multi <配置> --output ./episodes --enable-backend`。
- 新 `scene_guard.py` 参照 C4 的有界守护，自动选载具并确认 RPC `127.0.0.1:41451`，就绪后最长运行 300 s。未触碰 `runs/real-airsim-20260924/STOP5`。两次运行后写入新目录的 `STOP5`；`guard.stdout.log` 记录 `scene_stopped`、场景退出码 0，场景和守护进程均退出，41451 不再监听。旧守护和旧记录未改动。

## 结果与重读

| Episode | 真实结果 | 记录检查 |
| --- | --- | --- |
| `episodes/airsim-v02-single-example/` | CLI 退出 0；`success`、1 步、动作 `move-0` 成功；末次观察距目标 0.1336 m，低于 0.35 m 容差。 | 4 条事件；2 张 RGB PNG（77177、86107 字节）及 2 份 Depth（各 147456 字节）。 |
| `episodes/airsim-v02-controlled-failure-20260928/` | CLI 退出 1；`timeout`、1 步、动作期限 0.05 s；执行事件与轨迹均为 `action_deadline_exceeded`；末次观察距目标 0.5516 m。 | 4 条事件；2 张 RGB PNG（86687、86738 字节）及 2 份 Depth（各 147456 字节）。 |

两次 `core.plugin_cli show-result <episode_id> --output ./episodes` 均退出 0，重读与 `result.json` 一致。独立 `verify_evidence.py` 对轨迹前后观察中的所有 8 个传感器引用逐个检查：路径位于对应 episode 内、文件存在、RGB 有 PNG 签名、Depth 为 256×144 float32 所需的 147456 字节；两次观察均无 `missing_sensors`。终止事件和结果的 `cleanup_errors` 均为空。此项证明 Backend 清理调用没有报告错误；记录未包含清理后的物理触地、解除武装或控制释放状态的独立 RPC 读数，不能据此宣称物理落地。

## 能力与资源边界

两份 `metadata/capabilities.json` 都记录 `drone/move-to`、`drone/hover`，RGB/Depth 类型、`local_ned`、单机容量 1、`bounded_execution=true`，但 `sensor_resources={}`。这是 reset 前采集的能力声明；具体相机 `0` 的图像由实际 reset/observe 写入 episode，不能将图像存在视作事前资源声明成功。在安装版 Core/Pack 中，将 `required_sensor_resources={"rgb:0":"drone/rgb"}` 加入同一配置的内存副本，`_preflight` 以 `insufficient_capability: drone.v02.airsim/backend does not declare sensor resource rgb:0` 拒绝，未启动 Backend。场景元数据的 `resources={}`、固定 checksum `legacy-ue4-fixed-scene` 不校验实际地图内容、目标真值、seed 重置或动态资源装载。

## 冻结基线取证限制

运行前只核对了 EXE 哈希和两个 Git 跟踪的冻结文件状态，没有完整清点 manifest 所列文件；因此无法证明下述 `Saved/Config` 路径是在本次启动前还是启动中变化。运行后只读执行 `python -B simulator_contract/freeze_baseline.py --verify` 返回 `BASELINE_MISMATCH`：manifest 期望 1389 项，当前 1372 项；18 项缺失、1 项新增、所有 1371 个共有项的大小与 SHA-256 均匹配。缺失项中 6 个是先前已记录的 `PythonClient/.pytest_cache/` 文件；另有 12 个 `scene/WindowsNoEditor/ModularNeighborhood/Saved/Config/CrashReportClient/<ID>/CrashReportClient.ini` 缺失，ID 分别为：

`UE4CC-Windows-112B83D744FC25D883AA6AA38CE119E3`、`UE4CC-Windows-11FE11964FACE02E1DAA88A33AE5A6F7`、`UE4CC-Windows-2520D91F455D51D822DB538B4724ACC7`、`UE4CC-Windows-49BA9A4C4FB769B43706F48853AA2F0B`、`UE4CC-Windows-565C3C1D44A90DA281399497F270C490`、`UE4CC-Windows-69FE57B84F4991A9DA628F8ABC75B419`、`UE4CC-Windows-8B83CA014B88EECF8761F4BF708E10E6`、`UE4CC-Windows-91F123FB4E948C27F907E38824783722`、`UE4CC-Windows-A27F19324DDAA95A620C95914B932FD0`、`UE4CC-Windows-A73C24534E3F8676A8777F98521078CD`、`UE4CC-Windows-E8E5AAE840B1D134F8B9CD86C4DAF37B`、`UE4CC-Windows-F71BF809428FE96E448B409F9CB9B3A0`。

新增项为同目录 `UE4CC-Windows-183F4DB4446B2571419251BD1632CCBC/CrashReportClient.ini`，修改时间 2026-09-28 00:38:26。上述 13 个 CrashReportClient.ini 内容均为 112 字节、SHA-256 `ffd2f515d5b546c4d9f3a65c58af871cfe2c11812ae3cbd7a5b3a15718906b65`，但路径集合不同。`airsim-settings/drone.json` 当前 SHA-256 为 `345aeadde9ef223479fac81719a27d7308e95abb8ef84e560661e4c889df462e`；manifest 当前 SHA-256 为 `d04ed1734a186397c3b27b9f34e90335a70daebcd4373201628d62014c330990`，两个 Git 跟踪文件 `git diff --name-only` 为空。未运行 baseline writer，未修复、删除或覆盖任何冻结文件。

本次 v0.2 固定场景成功/可控失败、RGB/Depth、结果重读及无报错清理有真实 UE4 证据。相机资源事前能力声明、清理后的独立物理状态、基线文件集合差异、场景重置/真值及 F07/F08 正式规则确认仍需分别处理。
