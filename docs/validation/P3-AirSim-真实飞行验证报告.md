# P3 AirSim 真实飞行验证报告

日期：2026-09-24。场景：本机 Windows 图形环境中的冻结 UE4 ModularNeighborhood 包；AirSim RPC 127.0.0.1:41451。本文仅报告此次真实仿真飞行，不把无仿真测试当作飞行证据。原始数据保存在本地忽略目录 `runs/real-airsim-20260924/`，未提交或推送。

## 现场参数与资源

- 场景可执行文件：`材料-无人机遥感实习-AirSIM部分/scene/WindowsNoEditor/ModularNeighborhood/Binaries/Win64/ModularNeighborhood.exe`；SHA-256 `1fd385b9e35e68bba21f18007b8d001d521c2c128d213634ab88587590ba151e`。
- Python 3.13.5、msgpack 1.0.3；适配器和后端的 SHA-256、成功/失败两份试飞配置 SHA-256 见 `runs/real-airsim-20260924/p3-parameter-freeze.json`。成功例配置 SHA-256 `6d7cc231f6f816aa9afb0ba98298e252b26765adb66542414d28495629402c45`，可控失败例 `526f27514477aac0976b76a91b7345f4a3dbf907fbc81e08db38d1d51a79504d`，均在相应完整 episode 前写入。
- RPC 实际载具名为空字符串，平台 ID 为 `Drone`；相机 `0` 同时提供 256×144 RGB PNG 和 256×144 float32 深度帧。复制冻结 `drone.json` 到本地设置并以 `-settings` 或用户目录加载，均未使名为 `Drone` 的 RPC 载具出现；试验后已删除临时用户设置。
- 出生点约 NED (0,0,-0.1465)，检测到与 `Road_2` 的重叠；在 NED (4,0,-2) 重定位后让载具自然落至约 (4,0,-0.24)，作为本次 home。起飞后高度约 -2.24。航点 (4,0.5,-2)，移动速度上限 0.5 m/s，目标容差 0.35 m，到达及悬停速度上限 0.2 m/s，返航位置容差 0.5 m，任务时限 90 s。
- 最终兼容实现只对 `takeoff` 和 `hover` 的精确“RPC 返回 false”情况按实际高度、landed 状态及三轴速度限时确认；其他 RPC 错误仍失败。近地降落兼容判据为相对 home 高度误差不超过 0.5 m、三轴速度不超过 0.2 m/s，并已解除武装及释放 API 控制；此判据不能证明物理触地。

## 真实执行结果

| 运行 | 结果 | 可复核记录 |
| --- | --- | --- |
| 独立 Backend smoke 08 | 起飞、move_to、hover、返航和近地解除武装成功；移动后 NED (4,0.632,-1.976)、速度约 0.155 m/s；悬停后 (4,0.659,-1.977)、速度约 0.116 m/s；RGB/深度均可用。 | `runs/real-airsim-20260924/smoke-08/smoke_result.json` |
| 固定航线 ReachPoint 成功例 | CLI 退出码 0；任务成功，2 个动作、7 条事件；末次目标距离 0.1779 m、速度约 0.0887 m/s；路径采样估计 0.7296 m；返航收尾成功。 | `runs/real-airsim-20260924/p3-success/e23d9275d7fe48a6aaed9909d709b3d6/` |
| 短动作期限可控失败例 | 起飞后首个 move_to 的动作期限设为 0.05 s；执行记录为 transport timeout、任务失败，CLI 退出码 1；1 个动作、5 条事件；返航收尾成功。 | `runs/real-airsim-20260924/p3-controlled-failure/6763e872bfc946f6a0c3e7b377f5c505/` |

两份 episode 均通过 `python -B -m core.replay <episode目录>` 重读。成功例含 5 张 RGB、5 份深度，失败例含 3 张 RGB、3 份深度；全部引用可重读，PNG 签名正确，每份深度文件为 147456 字节。任务成功与最终清理分开记录。试验结束时实测 NED (4,0.0558,-0.2358)、速度三轴均为 0、landed=true；UE4 场景进程正常退出 0。

## 发现、修复及边界

- 早期 smoke 01 在与路面重叠的出生点把实际发生的起飞误判为失败。重定位到开阔路面后，旧场景的 takeoff/hover RPC 仍可返回 false，实际状态显示升空和减速；增加了严格限时状态确认，并用 FakeRpc 覆盖“false 且无升空/仍运动”必须失败。
- 直接将载具放在 (4,0,-0.25) 会退回出生点；改为从 (4,0,-2) 自然落地并等速度稳定。旧场景落地后偶尔持续报告 Flying，且 moveToZ 可能返回 false；清理现在按近地静止状态确认并始终尝试解除武装和释放控制。
- 所有诊断运行 smoke 01—08 的原始结果均保留；其中 02/03/04/06/07 记录了重定位、收尾阈值和场景断线问题。正式两份 episode 使用试飞前已保存的配置，没有事后回改其验收数值。
- F07 能力：reset、起飞、速度移动、状态读取、RGB、Depth、返航与兼容降落在此场景已实测；碰撞 RPC 做过局部诊断，但整个航程没有持续碰撞记录；目标真值未验证。不能据此宣称“零碰撞”或物理触地。场景重置、随机 seed 控制和目标真值仍待单独核验。
- F08 的此次参数已保存并用于两份完整试飞，但尚未经过三 Track 的正式规则确认。P3 的首次真实 ReachPoint 技术闭环已完成；正式验收仍需审查能力矩阵、碰撞/安全政策和第二可切换 Agent 的验收口径。当前结果不支持 P5 的统计性比较。

无仿真回归：`python -B -m pytest tests simulator_contract/tests -q`，147 项通过。冻结材料、`airsim-settings/drone.json` 和 legacy manifest 均未修改。
