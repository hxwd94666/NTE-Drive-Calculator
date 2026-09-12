# nte-mod-loader 组件记录

本机私用配套版本，Windows x64，Release；沿用现有代理的显式备用注入入口。

- 源码基底：`9d8431426624358e23f1873a273010f65a81007d`，使用本机集成工作区；源码基底与实际输入摘要分开记录。
- 二进制：`bin/nte-mod-loader.exe`，402,432 字节，未压缩。
- SHA-256：`9bf8b28b0fb55304d09bc61503062ebcb931d611b05c8006f1f78d624218e8e6`。
- 构建输入摘要：`5695da93785d5aa518825910dffefc966184bbf3d1c96f119f3d7241099fe114`。
- 构建：Visual Studio 2022 MSBuild，`native/nte-mod-loader/nte-mod-loader.sln`，Release/x64。
- 验证：构建成功，二进制本机路径扫描通过；新组合的游戏内注入仍待实测。
- 内嵌 requireAdministrator manifest；静态 MSVC 运行库。许可见 LICENSE 与 THIRD_PARTY_LICENSES.md。

Loader 只作为代理 DLL 无法被游戏加载时的显式备用方式。应用通过 `--dll` 指定配套的 `dwmapi.dll`，
由 stop event 与 owner PID 管理会话；不得静默启用或与代理方式同时加载。独立采集 DLL 仍由配套 Mod
工作区正常加载，不交给 manual-map。以上哈希记录当前交付版本，不改变用户显式选择可信 Loader 的行为。
