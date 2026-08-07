# 应用上下文与账号

## 模块定位

`src.app.context` 是应用级路径和账号状态的唯一组合根，负责把应用资源路径与当前账号可写路径
明确分开。

## 当前能力

- `ApplicationPaths` 提供应用目录、资源、静态库和共享库路径；
- `AccountContext` 提供账号 ID、用户库、配置、截图和日志目录；
- `AppContext.generation` 标识账号上下文代次；
- 账号切换时重建账号服务并通知各功能清理缓存；
- 长任务可以冻结账号、数据库路径和 generation，并在回调时复核。

## 数据边界

- 不直接保存业务数据；
- 账号数据只能写入当前 `AccountContext` 指定目录；
- 发行资源定位不能用于推导可写账号路径；
- 下层不得通过当前工作目录、环境变量或 MainWindow 动态字段猜测账号。

## 生命周期

账号切换前先停止或拒绝仍在运行的账号任务；切换后 generation 递增，旧 token、旧 worker 和旧
DAO 结果必须丢弃。应用退出由组合根依次关闭战报、同步、扫描、热键和其他后台资源。

## 当前限制

- 不支持运行中的长任务透明迁移到新账号；
- 旧账号结果只能丢弃，不能自动复制到新账号。

## 验证

主要覆盖 `test_app_context`、`test_account_user_database` 及各功能的旧 generation 丢弃测试。

## 主要实现

`src/app/context.py`、`src/features/accounts/`、`src/ui/app.py`。
