# nte-mod-loader 组件记录

- 当前二进制：从上游 `v0.4.3-build-146-bff9f56` 源码本地构建的 Windows x64 Loader
- `bin/nte-mod-loader.exe` SHA-256：`B73E0DEDA463418ABF55FAB53447A402F0084346AF068A584EBC7BA80EA2443D`
- 架构：Windows x64 PE；内嵌 `requireAdministrator` manifest
- 上游源码提交：`bff9f569ae19fc659b61a3a810407c01be5fc71a`
- 本地构建包：`nte-native-components-windows-x64-0.4.3-bff9f56.zip`
- 本地构建包 SHA-256：`513163EF44E8BE256C72366989A3BAEB52CCD278704443D39FBEFB7C819B9FBD`
- 构建工具链：Visual Studio 2022 Community，MSBuild `17.14.51.32402`
- 构建命令：`MSBuild.exe native\\nte-mod-loader\\nte-mod-loader.sln /t:Rebuild /p:Configuration=Release /p:Platform=x64 /m:1`
- 打包：UPX 5.2.0 `--best --lzma`，并通过 `upx -t`
- 许可证：上游主体 AGPL-3.0；内嵌依赖见 `THIRD_PARTY_LICENSES.md`
- MSVC 运行库：静态链接；正式二进制只导入 Windows 系统 DLL

Loader 只作为代理 DLL 无法被游戏加载时的显式备用方式。应用通过 `--dll` 传入已审计的
`dwmapi.dll` 绝对路径，并使用唯一命名 stop event 与 owner PID 管理本次会话。应用不得静默启用、
不得同时保留代理 DLL，也不得在停止超时后继续部署另一种加载方式。上述 SHA-256 用于记录随包发行
基线，不是运行时门禁；用户可用可信来源的同名 Loader 覆盖发行文件。
