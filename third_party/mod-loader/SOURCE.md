# nte-mod-loader 来源说明

`bin/nte-mod-loader.exe` 来自：

- 上游仓库：`https://github.com/kongbaiz/nte-dps-toolkit`
- 源码标签：`v0.4.3-build-146-bff9f56`
- 源码提交：`bff9f569ae19fc659b61a3a810407c01be5fc71a`
- 本地构建文件：`nte-native-components-windows-x64-0.4.3-bff9f56.zip`，不是 GitHub Release 资产
- 本地构建 ZIP SHA-256：`513163EF44E8BE256C72366989A3BAEB52CCD278704443D39FBEFB7C819B9FBD`
- Loader SHA-256：`B73E0DEDA463418ABF55FAB53447A402F0084346AF068A584EBC7BA80EA2443D`

该二进制监控官方启动器，通过内嵌 shim 的 `CreateProcessW` Hook 在游戏进程创建时 manual map
加载用户明确指定的 payload DLL。它不会被复制到游戏目录，由应用使用 UAC、stop event 和 owner PID
显式管理。
