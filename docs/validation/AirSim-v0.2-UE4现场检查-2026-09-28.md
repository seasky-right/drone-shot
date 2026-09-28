# AirSim v0.2 UE4 现场检查（2026-09-28）

本记录只说明 D5 执行前约 2026-09-28 00:30 CST 的环境状态。随后使用新的独立守护启动了场景并完成 v0.2 实飞；最终结果见 [AirSim v0.2 UE4 真实验证](AirSim-v0.2-UE4真实验证-2026-09-28.md)。本节当时的端点缺席不再是当前 D5 阻塞结论。

## 只读检查

| 检查 | 结果 |
| --- | --- |
| 固定场景可执行文件 | `材料-无人机遥感实习-AirSIM部分/scene/WindowsNoEditor/ModularNeighborhood/Binaries/Win64/ModularNeighborhood.exe` 存在；SHA-256 `1fd385b9e35e68bba21f18007b8d001d521c2c128d213634ab88587590ba151e`，与 [P3 真实飞行记录](P3-AirSim-真实飞行验证报告.md)中的文件哈希一致。 |
| UE4/AirSim 进程 | `Get-Process` 按 `UE4|Unreal|AirSim|ModularNeighborhood` 查询无结果；`Get-CimInstance Win32_Process` 未发现相应场景进程。 |
| RPC 端点 | `Get-NetTCPConnection -State Listen -LocalPort 41451` 无结果；`Test-NetConnection 127.0.0.1 -Port 41451` 返回 `TcpTestSucceeded: False`。 |
| 历史停机守护 | `runs/real-airsim-20260924/STOP5` 存在（0 字节，2026-09-24 17:38:53 修改）。未移除或绕过。 |

## D5 结论

只读检查时没有运行中的场景。旧 `STOP5` 未移除或绕过；后续试验采用 `runs/real-airsim-v02-20260928/` 下的新守护、新 `STOP5` 和独立 episode 目录。本记录不充当实飞结果，D5 结论以新验证记录为准。
