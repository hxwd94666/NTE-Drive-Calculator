# nte-mod-loader 来源说明

`bin/nte-mod-loader.exe` 来自：

- 上游仓库：`https://github.com/kongbaiz/nte-dps-toolkit`
- 发布标签：`v0.4.1-build-137-385f5de`
- 源码提交：`385f5de4b70e3ae3dfb01a17ef735c36a202b2e3`
- Release 文件：`nte-dps-tool-windows-x64.zip`
- Release ZIP SHA-256：`6234F55C221612E60E0CB69D3C36B51693D4040481CDB57B63C301EC5CC244BB`
- Loader SHA-256：`398039F5314D8E0843E0DF2F144E7081A3E2BBE9879AFB68A671C9E360AF9C80`

该二进制监控官方启动器，通过内嵌 shim 的 `CreateProcessW` Hook 在游戏进程创建时 manual map
加载用户明确指定的 payload DLL。它不会被复制到游戏目录，由应用使用 UAC、stop event 和 owner PID
显式管理。
