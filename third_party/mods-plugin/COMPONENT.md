# nte-mods-plugin 组件记录

本机增强战报试用插件；保留原有代理与 Loader 入口、背包脚本和战斗时钟脚本。

- `bin/dwmapi.dll` SHA-256：`9b59c1dd756202506dc0f0f6306aab6163980f2c01f5e8588eab97f7b132ffda`
- 源码基底：`9d8431426624358e23f1873a273010f65a81007d`，包含未提交的专用适配。
- Windows x64，Release，1,117,696 字节；MSVC/UCRT 动态运行库。
- 现有工作区 worker 使用固定路径正常加载 `NTE_Capture.dll`；不另建平台、注入器或热卸载入口。
- 工作区同时交付独立采集 DLL、native-capture.json、许可与通知；DLL 版本/哈希以该清单为准。
- 独立采集只在 Core 请求后开始，更新 DLL 后需重启游戏。
- 许可证：AGPL-3.0，沿用本目录 LICENSE；独立采集 DLL 的 GPL-3.0/Detours MIT 声明随工作区单独提供。

已通过 Release 构建和二进制路径检查，代理/Loader 与采集 DLL 在真实游戏中的共存仍待实机验收。
部署继续保留原有同名 DLL 的备份与还原；运行时 SDK 缓存不进入发行模板。
