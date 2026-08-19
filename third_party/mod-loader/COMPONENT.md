# nte-mod-loader 组件记录

- 当前二进制：上游正式发布 `v0.4.1-build-137-385f5de` 中的 Windows x64 Loader
- `bin/nte-mod-loader.exe` SHA-256：`398039F5314D8E0843E0DF2F144E7081A3E2BBE9879AFB68A671C9E360AF9C80`
- 架构：Windows x64 PE；内嵌 `requireAdministrator` manifest
- 上游源码提交：`385f5de4b70e3ae3dfb01a17ef735c36a202b2e3`
- 许可证：上游主体 AGPL-3.0；内嵌依赖见 `THIRD_PARTY_LICENSES.md`
- MSVC 运行库：静态链接；正式二进制只导入 Windows 系统 DLL

Loader 只作为代理 DLL 无法被游戏加载时的显式备用方式。应用通过 `--dll` 传入已审计的
`dwmapi.dll` 绝对路径，并使用唯一命名 stop event 与 owner PID 管理本次会话。应用不得静默启用、
不得同时保留代理 DLL，也不得在停止超时后继续部署另一种加载方式。
