# nte-mods-plugin 来源说明

当前 `bin/dwmapi.dll` 来自上游正式发布
`v0.4.1-build-138-12ef5e8/nte-dps-tool-windows-x64.zip`：

- 工作区元数据版本：`0.3.6`
- SHA-256：`46DA088EB62EB0A02C5488356F5B47E63A25E3D887D6B365DB98F3A7828EA5D5`
- 公共组件标识：`NTE_DPS_TOOL_MODS_PLUGIN_V1`
- 已知运行机制：内置 Dumper-7，根据当前游戏校验值生成或复用本地 `NTE_SDK.bin`
- 上游仓库：`https://github.com/kongbaiz/nte-dps-toolkit`
- 发布标签：`v0.4.1-build-138-12ef5e8`
- 源码提交：`12ef5e865bbfb843f1814fd9c9d4b470034c5841`
- Release ZIP SHA-256：`8DE58DA2449445AA5B218E713E2FFD1F0FB974AA5FDEC48F5CD2D031AFAC1C3F`
- 运行库依赖：Microsoft Visual C++ 2015–2022 Redistributable x64

计算器仅随该 DLL 分发受限工作区中的 `equipment.nte` 与 `combat-clock.nte`。`NTE_SDK.bin` 和 `NTE_SDK.checksum` 是用户本机运行时缓存，不随发行包分发，也不由工作区刷新覆盖。

上游 0.4.1 同时增加了引擎模块就绪等待、偏移解析重试和 manual-map 适配；本项目不修改该二进制。
