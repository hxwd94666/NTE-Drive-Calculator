# nte-mods-plugin 组件记录

- 当前二进制：抓包侧提供的动态 SDK 构建（2026-08-15）
- 工作区元数据版本：`0.3.6`（来自 `workspace/mods-plugin.version`）
- `bin/dwmapi.dll` SHA-256：`27F8C418D961A5CC5645FBA5E98E84DF0CB92EF3FF83FDE264220C510FC58D34`
- 架构：Windows x64 PE；带 `NTE_DPS_TOOL_MODS_PLUGIN_V1` 公共签名
- 运行时适配：DLL 内置 Dumper-7；首次遇到游戏映像时生成 `NTE_SDK.bin`，随后仅在保存的 SDK 校验值与当前游戏校验值一致时复用，不一致时自动重新生成
- 默认工作区：只启用 `equipment` 与 `combat-clock`；运行时 SDK 缓存和额外 Mod 不进入发行模板
- 上游发布标签/源码提交：`UNSPECIFIED`（随二进制未提供）；发布前由维护者补全
- 许可证来源：沿用本目录 `LICENSE`；发布前由维护者确认该二进制对应的上游许可证与来源提交

应用只在用户确认后部署 DLL 到所选 `HTGame.exe` 同目录。脚本和 SDK 缓存保留在应用可写数据目录，并通过当前用户注册表 `Software\NTE DPS Tool\Mods Plugin` 的 `Workspace` 值提供给 DLL。工作区刷新只更新受管脚本，不覆盖 `NTE_SDK.bin` 与 `NTE_SDK.checksum`。

部署逻辑必须保留原有 DLL 备份与还原能力。
