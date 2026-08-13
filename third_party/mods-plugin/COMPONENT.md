# nte-mods-plugin 组件记录

- 上游项目：`kongbaiz/nte-dps-toolkit`
- 当前二进制版本：`0.3.8`
- 上游发布：`v0.3.8-build-98-efe9473`
- 上游提交：`efe94737b5b7bd5ed796b97ca6161e6e53b34d77`
- 构建源码提交：`9b88da5ae5182e0a18ddc7ce69ba6594df61e3a2`；插件相关目录与上述发布提交一致
- 本地兼容补丁：为正式服映像 `0x1066A000`（PE CheckSum `0x0FDCF5DD`）加入已验证偏移
  `AppendName=0x016491C0`、`GWorld=0x0F08CDB0`，Viewport Tick/ProcessEvent 索引保持 `100`/`0x4C`
- 构建方式：Visual Studio 2022 MSVC v143，`Release|x64`，0 警告、0 错误
- `bin/dwmapi.dll` SHA-256：`BFC54DB88A0F9738AA31A4447EB5ADD6EFEFFFBF28E466C75A5D1B84211CB59E`
- IPC：`\\.\pipe\nte-mods-plugin-v7`

`bin/dwmapi.dll` 与 `workspace/` 下的受限 NTE C++ v5 程序必须作为同一发布
组件更新。应用只在用户明确确认后把 DLL 部署到所选 `HTGame.exe` 同目录；
脚本保留在应用可写数据目录，并通过当前用户注册表
`Software\NTE DPS Tool\Mods Plugin` 的 `Workspace` 值提供给 DLL。

部署逻辑必须保留原有同名 DLL 的备份与还原能力。应用更新内置脚本时只覆盖
仍与上次托管版本一致的文件，保留用户自行编辑的脚本和启用集合。
