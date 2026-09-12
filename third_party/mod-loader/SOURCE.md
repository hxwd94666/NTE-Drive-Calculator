# nte-mod-loader 来源说明

基于 [nte-dps-toolkit](https://github.com/kongbaiz/nte-dps-toolkit) 的现有 Loader 源码，本机配套构建日期 2026-09-10。
源码基底为 `9d8431426624358e23f1873a273010f65a81007d`；实际输入摘要、二进制大小与 SHA-256 见 COMPONENT.md，未执行公开发布。

Loader 监控启动器，通过内嵌 shim 的 CreateProcessW Hook 加载显式指定的 payload DLL；
由应用通过 UAC、stop event 和 owner PID 管理，不复制到游戏目录。
沿用 AGPL-3.0 和随包第三方许可；私有源码、调试符号、账号数据不进入交付包。
