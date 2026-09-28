# AirSim v0.2 UE4 冻结基线路径取证（2026-09-28）

本记录只读检查 `CrashReportClient.ini` 路径差异及后续隔离复验条件。没有启动 UE4、运行 baseline writer，或修改冻结材料、`airsim-settings/drone.json` 与 manifest。D5 实飞结果和完整差异见 [真实验证记录](AirSim-v0.2-UE4真实验证-2026-09-28.md)。

## 可确认的时间线

- 2026-09-21 的原始日志 `docs/validation/logs/2026-09-21-frozen-baseline.txt` 记录 `VERIFIED files=1389`；2026-09-23 的 [P2 报告](P2实现验收报告.md)记录仅六个 `.pytest_cache` 文件缺失，其余 1,383 项匹配。因此 manifest 中的 12 个 `Saved/Config/CrashReportClient/<ID>/CrashReportClient.ini` 至少在 P2 核验时仍存在且内容匹配。
- 2026-09-24 和 09-25 均有真实场景启动记录；09-25 场景日志从 19:49:38 开始。现有记录没有这两次启动前后的完整 manifest 文件清单。
- 2026-09-28 守护日志创建于 00:38:25；当前场景日志于 00:38:26 开始，`CrashReportClient` 父目录的最后写入时间及唯一现存子目录和 INI 的创建时间均为 00:38:26。该新增 INI 为 112 字节，SHA-256 `ffd2f515d5b546c4d9f3a65c58af871cfe2c11812ae3cbd7a5b3a15718906b65`，与 manifest 记录的 12 个旧路径内容哈希相同。其文本包含 `CrashConfigPurgeDays=2`。
- 运行后的完整比对显示缺失 12 个旧 INI 路径、新增一个同目录 INI 路径；其余共有项哈希和大小均匹配。Git 仅跟踪 manifest，不跟踪课程场景文件，无法从 Git 恢复这些旧目录的状态。

新增 INI 与 09-28 的 UE4 启动同秒出现，符合程序生成配置的特征；`CrashConfigPurgeDays=2` 与旧目录被轮换清理的现象相符。但没有 09-28 启动前的完整清单，也没有记录具体删除动作的日志，因此不能证明 12 个旧路径是在本次启动中消失，或确定删除者。当前冻结目录扫描结果只有 1,372 个清单范围内的文件；据此创建的副本不能称为原始 1,389 项冻结基线。

## 隔离复验条件

`WindowsNoEditor` 场景树当前为 74 个文件、2,489,601,679 字节；F: 在检查时剩余 70,156,890,112 字节。09-25 和 09-28 的守护脚本均以 `ModularNeighborhood/Binaries/Win64/ModularNeighborhood.exe` 启动，并将工作目录设为 EXE 所在目录；UE4 日志显示以相对路径挂载 `ModularNeighborhood/Content/Paks`。复制时须保留整个 `WindowsNoEditor` 层级，包括 `Engine`、`ModularNeighborhood` 和根目录文件。复制后从副本的 EXE 启动，并将工作目录设为副本 EXE 的父目录；此复制启动方式尚未实测。

复验前，在冻结目录和副本之外保存两者各自的完整相对路径、字节数及 SHA-256 清单，包含 `Saved/Config`；确认复制源和副本逐项一致，并记录当前源与原 manifest 的既有差异。复验后重新采集两份清单，要求冻结源与运行前完全一致，只归纳副本的新增、缺失或内容变化。所有记录放在 `runs/` 的独立目录；不得把当前副本的结果写成原始 1,389 项 manifest 的通过结果，也不得用 baseline writer 消除差异。项目 [README](../../README.md) 指出外部资源再分发权尚未确定，因此副本仅作本机验证，不上传或发布。
