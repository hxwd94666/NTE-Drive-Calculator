# nte-core 对应来源

本机私用试验版本，沿用 [nte-dps-toolkit](https://github.com/kongbaiz/nte-dps-toolkit) 的采集基底与 AGPL-3.0 许可。
本次带专用原生采集适配，公开上游源码不能完整重建本二进制；私有源码、账号数据和调试符号不随本包交付。

- 构建日期：2026-09-10；版本 `0.4.4`；Windows x64。
- 源码基底：`9d8431426624358e23f1873a273010f65a81007d`，包含当前工作区未提交修改。
- 构建：`cargo build --release --locked --bin nte-core --no-default-features --features cli`，进程内配置源码路径映射。
- 本次未压缩，1,482,752 字节；二进制和构建输入清单 SHA-256 以 COMPONENT.md 为准。
- DLL、mods 插件和独立分析组件分别构建，成套交付；未进行公开发布。
