# 设置、更新与日志

## 模块定位

管理账号设置、环境诊断、更新流程和结构化日志，不承载业务计算。

## 当前能力

- 账号设置写当前账号库或账号配置目录；
- Npcap、nte-core、dwmapi、插件和资源诊断；
- Mirror 更新检查、下载和安装编排；
- INFO 常驻日志；
- 用户开启运行日志后创建独立时间戳 DEBUG 文件；
- 账号切换时关闭旧 sink 并在新账号目录重建；
- 核心操作记录 operation ID、阶段、耗时和安全上下文。

## 数据边界

设置页只接收 `AppContext`。更新 Controller/Integration 独占 URL、下载和安装器启动。日志不得记录
Token、CDK、完整 RPC、背包、UID 列表、OCR 全文、截图或用户绝对路径。

## 二进制边界

根目录本机 `nte-core.exe`、`dwmapi.dll` 和插件副本被 Git ignore。`third_party/` 只保存明确晋升的
发行组件，更新时必须记录来源、版本、许可和打包断言。

## 验证

主要覆盖 settings、update、runtime logging、observability、packaging 和 Windows preflight 测试。

## 主要实现

`src/features/settings/`、`src/features/update/`、`src/observability/`、
`tools/windows_validation/`、`tools/release/`。
