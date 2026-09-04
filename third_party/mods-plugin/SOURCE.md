# nte-mods-plugin 来源说明

当前 `bin/dwmapi.dll` 从上游提交 `aa19b2f` 基线叠加灵可时停类型修复后本地构建：

- 工作区元数据版本：`0.3.6`
- SHA-256：`05B9CBED26BA6FD7837CA3EA21D78CAA249B953CB76A22E954A486A3406D4B6F`
- 公共组件标识：`NTE_DPS_TOOL_MODS_PLUGIN_V1`
- 已知运行机制：内置 Dumper-7，根据当前游戏校验值生成或复用本地 `NTE_SDK.bin`
- 上游仓库：`https://github.com/kongbaiz/nte-dps-toolkit`
- 源码分支：`origin/codex/fix-follow-up-source-attribution`
- 源码基线提交：`aa19b2fa522f46735843efb503d8800bec981c70`
- 本地源码修复：在 `native/nte-mods-plugin/src/host_api.cpp` 中将 `EPausedGameType::PG_LinkoEffect = 6` 纳入战斗时钟采样和转发掩码
- 修复提交：<https://github.com/ternary-chen/nte-dps-toolkit/commit/4b7653bf5e866c4ae4d45524653ead5d9178e1c1>
- 包含该修复的公开源码提交：<https://github.com/ternary-chen/nte-dps-toolkit/commit/24bb078fe38675d1719d24e92465a313baf65b3e>
- 本地构建文件：直接生成的 Release x64 `dwmapi.dll`，未生成 ZIP 构建包
- 运行库依赖：Microsoft Visual C++ 2015–2022 Redistributable x64

计算器仅随该 DLL 分发受限工作区中的 `equipment.nte` 与 `combat-clock.nte`。`NTE_SDK.bin` 和 `NTE_SDK.checksum` 是用户本机运行时缓存，不随发行包分发，也不由工作区刷新覆盖。

本项目随包副本保持未压缩，并保留其动态 MSVC 运行库依赖。本次仅替换 DLL；工作区元数据和默认脚本未变化。
