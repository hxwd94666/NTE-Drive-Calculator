# nte-mods-plugin 来源说明

当前 `bin/dwmapi.dll` 从上游标签 `v0.4.3-build-146-bff9f56` 对应源码本地构建：

- 工作区元数据版本：`0.3.6`
- SHA-256：`4CAA672401237D511CFF756F2694C7785B20B4C615FEFBD9EF09282E90C10F07`
- 公共组件标识：`NTE_DPS_TOOL_MODS_PLUGIN_V1`
- 已知运行机制：内置 Dumper-7，根据当前游戏校验值生成或复用本地 `NTE_SDK.bin`
- 上游仓库：`https://github.com/kongbaiz/nte-dps-toolkit`
- 源码标签：`v0.4.3-build-146-bff9f56`
- 源码提交：`bff9f569ae19fc659b61a3a810407c01be5fc71a`
- 本地构建文件：`nte-native-components-windows-x64-0.4.3-bff9f56.zip`，不是 GitHub Release 资产
- 本地构建 ZIP SHA-256：`513163EF44E8BE256C72366989A3BAEB52CCD278704443D39FBEFB7C819B9FBD`
- 运行库依赖：Microsoft Visual C++ 2015–2022 Redistributable x64

计算器仅随该 DLL 分发受限工作区中的 `equipment.nte` 与 `combat-clock.nte`。`NTE_SDK.bin` 和 `NTE_SDK.checksum` 是用户本机运行时缓存，不随发行包分发，也不由工作区刷新覆盖。

本项目不修改该二进制；随包副本保持未压缩，并保留其动态 MSVC 运行库依赖。
