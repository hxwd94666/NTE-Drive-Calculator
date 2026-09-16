# nte-mod-loader 组件记录

Windows x64 Release；用于标准加载同一无界面采集运行时的显式备用入口。

- 源码基底：`3e3bcd05583c369cfecc477aaa0adc933591841b`；Loader 源码目录无未提交修改。
- 构建输入摘要：`57aa89f001d0202d85fd8a11df408f2e806cc32e2cdce6803001532c56dbc2ae`。
- 二进制：`bin/nte-mod-loader.exe`，426,496 字节，未压缩。
- SHA-256：`cd7254b3f4ecc168dccbf113a950011309dbf4d902008b45c58a24ab0f16d536`。
- 构建：Visual Studio 2022 MSBuild，Release/x64，主程序与内嵌 shim 成套构建。
- 协议：能力查询 schema 1，shim 协议 3，支持 `loadlibrary` 与 `nte_capture_runtime_v1`。
- 验证：原生专项用例、只读能力查询及内嵌资源一致性检查通过；新版游戏内启动尚待实测。
- 内嵌 requireAdministrator manifest；许可见 LICENSE 与 THIRD_PARTY_LICENSES.md。

原生采集使用 `loadlibrary` 加载 `NTE_Capture.dll`，不将独立采集 DLL 交给 manual-map。
Loader 按 stop event 和 owner PID 管理会话；升级由安装器更新应用内 EXE，运行目录中的采集 DLL
按当前整包哈希核对，相同版本保留，旧版在游戏退出后更新。Loader 不复制到游戏目录。
