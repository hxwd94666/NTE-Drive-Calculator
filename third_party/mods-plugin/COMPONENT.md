# nte-mods-plugin 组件记录

- 上游项目：`kongbaiz/nte-dps-toolkit`
- 当前二进制版本：`0.3.6`
- 上游发布：`v0.3.6-build-86-a7c6793`
- 上游提交：`a7c6793`
- 来源压缩包：`nte-dps-tool-windows-x64.zip`
- 来源压缩包 SHA-256：`B18F00D4A517D2686373C04F7CF08ECF65BD3CF8F57F5870C1E195B3A63417A9`
- `bin/dwmapi.dll` SHA-256：`AC1F62FDCCDE7D4611BC3E027A8CEAE9312109D4BCE32575E7D5CF62602A301A`
- IPC：`\\.\pipe\nte-mods-plugin-v7`

`bin/dwmapi.dll` 与 `workspace/` 下的受限 NTE Script 程序必须作为同一发布
组件更新。应用只在用户明确确认后把 DLL 部署到所选 `HTGame.exe` 同目录；
脚本保留在应用可写数据目录，并通过当前用户注册表
`Software\NTE DPS Tool\Mods Plugin` 的 `Workspace` 值提供给 DLL。

部署逻辑必须保留原有同名 DLL 的备份与还原能力。应用更新内置脚本时只覆盖
仍与上次托管版本一致的文件，保留用户自行编辑的脚本和启用集合。
