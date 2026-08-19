# Mods 插件版本适配

本项目当前发行基线为 nte-dps-toolkit
`v0.4.1-build-137-385f5de`：

- `dwmapi.dll`：`6D5A4B2C48BBEE97C6463BCA36AA2E3B2AA00849991EA6F0DDF7A0F28C76F133`；
- `nte-mod-loader.exe`：`398039F5314D8E0843E0DF2F144E7081A3E2BBE9879AFB68A671C9E360AF9C80`；
- 工作区元数据：`0.3.6`；
- DLL 公共签名：`NTE_DPS_TOOL_MODS_PLUGIN_V1`；
- IPC：ABI v4 / IPC v7，命名管道 `nte-mods-plugin-v7`。

## 加载方式

代理 DLL 是默认方式。应用在游戏关闭时备份既有 `dwmapi.dll`，部署已审计 DLL并注册脚本工作区；还原时
只有目标 SHA 仍等于本应用部署记录才允许覆盖或删除。

备用 Loader 只用于游戏不加载代理 DLL的环境。应用显式传入 payload 绝对路径，以管理员权限启动，并用
唯一 stop event 与 owner PID 管理会话。启动前游戏目录不得存在任何 `dwmapi.dll`，Loader 运行时不得部署
代理方式。停止事件名严格遵循上游 `Local\\NTE-DPS-TOOL-ModLoader-` 加 16 位小写十六进制字符的
控制协议。正常停止使用 stop event，并为上游清理已注入启动器保留 15 秒；停止超时属于失败，不能继续
切换。应用在启动前保存工作区注册表基线，UAC 取消、启动失败、正常停止和应用退出都尝试恢复；若当前
值已被其他程序接管则保持不动。

## 运行依赖

0.4.1 DLL动态依赖 Microsoft Visual C++ 2015–2022 Redistributable x64 的 `MSVCP140.dll`、
`VCRUNTIME140.dll` 和 `VCRUNTIME140_1.dll`。Loader 静态链接 MSVC Runtime。诊断必须分别报告文件缺失、
Loader 缺失、payload 缺失、UAC取消、管道缺失和停止超时。

## 游戏或上游更新后的复核

1. 核对上游 tag、commit、下载包 SHA和两颗二进制 SHA；
2. 核对 DLL导出、公共签名、工作区版本、脚本语法版本、ABI和 IPC；
3. 核对新增 Windows/MSVC依赖，更新组件记录与许可证；
4. 验证代理方式的备份、部署、工作区注册、管道和还原；
5. 在代理不生效的环境验证 Loader 的 UAC、启动器监控、游戏注入、管道和协作停止；
6. 游戏更新后验证引擎模块就绪等待、offset解析、SDK缓存重建、时停查询和一次受控装配；
7. 任何失败均保留旧组件和用户脚本，不以调试 DLL或未记录二进制替换发行组件。
