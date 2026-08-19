# nte-mod-loader 来源说明

`bin/nte-mod-loader.exe` 来自：

- 上游仓库：`https://github.com/kongbaiz/nte-dps-toolkit`
- 发布标签：`v0.4.1-build-138-12ef5e8`
- 源码提交：`12ef5e865bbfb843f1814fd9c9d4b470034c5841`
- Release 文件：`nte-dps-tool-windows-x64.zip`
- Release ZIP SHA-256：`8DE58DA2449445AA5B218E713E2FFD1F0FB974AA5FDEC48F5CD2D031AFAC1C3F`
- Loader SHA-256：`7DEC440B5935162AD2F67B95E512CCE619460AD4D89E26A3B5011A9411333E48`

该二进制监控官方启动器，通过内嵌 shim 的 `CreateProcessW` Hook 在游戏进程创建时 manual map
加载用户明确指定的 payload DLL。它不会被复制到游戏目录，由应用使用 UAC、stop event 和 owner PID
显式管理。
