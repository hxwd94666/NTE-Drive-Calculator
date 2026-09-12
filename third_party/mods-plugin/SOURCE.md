# nte-mods-plugin 来源说明

本机私用试验插件，基于 [nte-dps-toolkit](https://github.com/kongbaiz/nte-dps-toolkit)，沿用 AGPL-3.0。
源码基底 `9d8431426624358e23f1873a273010f65a81007d`，包含当前未提交的专用加载适配；公开上游不足以
完整重建本次二进制。私有源码、SDK 与调试符号不随包提供，未执行公开发布。

2026-09-10 使用 MSBuild Release/x64 构建，SHA-256 见 COMPONENT.md。保留内置 Dumper-7、原代理与
Loader 入口；在既有 workspace worker 正常加载独立采集 DLL，不改变 Loader 的 manual-map payload。
工作区默认脚本仍为 equipment.nte 与 combat-clock.nte；生成的 NTE_SDK.bin/checksum 不随包交付。
独立采集 DLL 来源、输入摘要与许可见工作区 native-capture.json 和 NTE_Capture 许可通知。
