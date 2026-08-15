# nte-mods-plugin 来源说明

当前 `bin/dwmapi.dll` 为 2026-08-15 提供的 x64 动态 SDK 构建：

- 工作区元数据版本：`0.3.6`
- SHA-256：`27F8C418D961A5CC5645FBA5E98E84DF0CB92EF3FF83FDE264220C510FC58D34`
- 公共组件标识：`NTE_DPS_TOOL_MODS_PLUGIN_V1`
- 已知运行机制：内置 Dumper-7，根据当前游戏校验值生成或复用本地 `NTE_SDK.bin`
- 上游仓库、发布标签与源码提交：`UNSPECIFIED`（未随本次二进制投放提供）

计算器仅随该 DLL 分发受限工作区中的 `equipment.nte` 与 `combat-clock.nte`。`NTE_SDK.bin` 和 `NTE_SDK.checksum` 是用户本机运行时缓存，不随发行包分发，也不由工作区刷新覆盖。

维护者在公开发布前补充上游仓库、提交、构建方式与许可证对应关系。
