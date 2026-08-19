# nte-mods-plugin 来源说明

当前 `bin/dwmapi.dll` 来自上游正式发布
`v0.4.1-build-137-385f5de/nte-dps-tool-windows-x64.zip`：

- 工作区元数据版本：`0.3.6`
- SHA-256：`6D5A4B2C48BBEE97C6463BCA36AA2E3B2AA00849991EA6F0DDF7A0F28C76F133`
- 公共组件标识：`NTE_DPS_TOOL_MODS_PLUGIN_V1`
- 已知运行机制：内置 Dumper-7，根据当前游戏校验值生成或复用本地 `NTE_SDK.bin`
- 上游仓库：`https://github.com/kongbaiz/nte-dps-toolkit`
- 发布标签：`v0.4.1-build-137-385f5de`
- 源码提交：`385f5de4b70e3ae3dfb01a17ef735c36a202b2e3`
- Release ZIP SHA-256：`6234F55C221612E60E0CB69D3C36B51693D4040481CDB57B63C301EC5CC244BB`
- 运行库依赖：Microsoft Visual C++ 2015–2022 Redistributable x64

计算器仅随该 DLL 分发受限工作区中的 `equipment.nte` 与 `combat-clock.nte`。`NTE_SDK.bin` 和 `NTE_SDK.checksum` 是用户本机运行时缓存，不随发行包分发，也不由工作区刷新覆盖。

上游 0.4.1 同时增加了引擎模块就绪等待、偏移解析重试和 manual-map 适配；本项目不修改该二进制。
