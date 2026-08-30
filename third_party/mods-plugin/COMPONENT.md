# nte-mods-plugin 组件记录

- 当前二进制：从上游 `v0.4.3-build-146-bff9f56` 源码本地构建的 Windows x64 插件
- 工作区元数据版本：`0.3.6`（来自 `workspace/mods-plugin.version`）
- `bin/dwmapi.dll` SHA-256：`4CAA672401237D511CFF756F2694C7785B20B4C615FEFBD9EF09282E90C10F07`
- 架构：Windows x64 PE；带 `NTE_DPS_TOOL_MODS_PLUGIN_V1` 公共签名
- 运行时适配：DLL 内置 Dumper-7；首次遇到游戏映像时生成 `NTE_SDK.bin`，随后仅在保存的 SDK 校验值与当前游戏校验值一致时复用，不一致时自动重新生成
- 默认工作区：只启用 `equipment` 与 `combat-clock`；运行时 SDK 缓存和额外 Mod 不进入发行模板
- MSVC 运行库：动态链接 `MSVCP140.dll`、`VCRUNTIME140.dll` 与 `VCRUNTIME140_1.dll`；应用部署诊断必须在启用前检查
- 上游源码标签：`v0.4.3-build-146-bff9f56`
- 上游源码提交：`bff9f569ae19fc659b61a3a810407c01be5fc71a`
- 本地构建包：`nte-native-components-windows-x64-0.4.3-bff9f56.zip`
- 本地构建包 SHA-256：`513163EF44E8BE256C72366989A3BAEB52CCD278704443D39FBEFB7C819B9FBD`
- 构建工具链：Visual Studio 2022 Community，MSBuild `17.14.51.32402`
- 构建命令：`MSBuild.exe native\\nte-mods-plugin\\nte-mods-plugin.sln /t:Clean,Build /p:Configuration=Release /p:Platform=x64 /m`
- 许可证：AGPL-3.0，沿用本目录 `LICENSE`

应用只在用户确认后部署 DLL 到所选 `HTGame.exe` 同目录。脚本和 SDK 缓存保留在应用可写数据目录，并通过当前用户注册表 `Software\NTE DPS Tool\Mods Plugin` 的 `Workspace` 值提供给 DLL。工作区刷新只更新受管脚本，不覆盖 `NTE_SDK.bin` 与 `NTE_SDK.checksum`。DLL 保持未压缩。

部署逻辑必须保留原有 DLL 备份与还原能力。
