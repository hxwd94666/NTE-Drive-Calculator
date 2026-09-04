# nte-mods-plugin 组件记录

- 当前二进制：从上游提交 `aa19b2f` 基线叠加灵可时停类型修复后，本地构建的 Windows x64 插件
- 工作区元数据版本：`0.3.6`（来自 `workspace/mods-plugin.version`）
- `bin/dwmapi.dll` SHA-256：`05B9CBED26BA6FD7837CA3EA21D78CAA249B953CB76A22E954A486A3406D4B6F`
- 架构：Windows x64 PE；带 `NTE_DPS_TOOL_MODS_PLUGIN_V1` 公共签名
- 运行时适配：DLL 内置 Dumper-7；首次遇到游戏映像时生成 `NTE_SDK.bin`，随后仅在保存的 SDK 校验值与当前游戏校验值一致时复用，不一致时自动重新生成
- 默认工作区：只启用 `equipment` 与 `combat-clock`；运行时 SDK 缓存和额外 Mod 不进入发行模板
- MSVC 运行库：动态链接 `MSVCP140.dll`、`VCRUNTIME140.dll` 与 `VCRUNTIME140_1.dll`；应用部署诊断必须在启用前检查
- 上游源码分支：`origin/codex/fix-follow-up-source-attribution`
- 上游源码基线提交：`aa19b2fa522f46735843efb503d8800bec981c70`
- 本地源码修复：`native/nte-mods-plugin/src/host_api.cpp` 将 `EPausedGameType::PG_LinkoEffect = 6` 纳入战斗时钟采样和转发掩码
- 修复提交：`4b7653bf5e866c4ae4d45524653ead5d9178e1c1`；包含该修复的公开源码提交：`24bb078fe38675d1719d24e92465a313baf65b3e`
- 本地构建形式：直接构建并同步 `dwmapi.dll`，未生成 ZIP 构建包
- 构建工具链：Visual Studio 2022 Community，MSBuild `17.14.51.32402`
- 构建命令：`MSBuild.exe native\\nte-mods-plugin\\nte-mods-plugin.sln /t:Clean,Build /p:Configuration=Release /p:Platform=x64 /m`
- 许可证：AGPL-3.0，沿用本目录 `LICENSE`

应用只在用户确认后部署 DLL 到所选 `HTGame.exe` 同目录。脚本和 SDK 缓存保留在应用可写数据目录，并通过当前用户注册表 `Software\NTE DPS Tool\Mods Plugin` 的 `Workspace` 值提供给 DLL。工作区刷新只更新受管脚本，不覆盖 `NTE_SDK.bin` 与 `NTE_SDK.checksum`。DLL 保持未压缩。

部署逻辑必须保留原有 DLL 备份与还原能力。
