# 核心日志事件目录

本文记录可用于问题检索的稳定事件族。事件名使用英文点分格式，中文说明只用于阅读，不作为程序判断条件。

## 公共字段

| 字段 | 含义 |
| --- | --- |
| `event` | 稳定事件名 |
| `feature` | 功能域 |
| `operation_id` | 一次用户操作跨 Controller、worker、Service 和 Integration 的关联 ID |
| `account_id` | 内部账号 ID；不记录账号显示名称 |
| `context_generation` | AppContext 账号切换代次 |
| `snapshot_id` | 固定库存快照 |
| `job_id` | 装配等持久化任务 ID |
| `phase` | `started`、`succeeded`、`failed` 或具体阶段 |
| `duration_ms` | 阶段耗时 |
| `result` | 安全的结果摘要 |

未适用字段不写入，不使用虚假空值占位。

## 事件族

| 功能 | 事件前缀与关键事件 | 主要排查内容 |
| --- | --- | --- |
| 应用 | `application.started`、`application.stopping` | 版本、启动和退出 |
| 账号 | `account.switch_*` | 阻止、取消、切换阶段、失败和完成 |
| 数据迁移 | `database.shape_bonus_migration_*` | 旧版额外形状迁移、事务失败 |
| nte-core 同步 | `inventory_sync.*` | 连接、候选快照、稳定等待、提交、保留策略和停止原因；已有/候选/提交快照记录驱动、空幕、已装备、锁定、角色实例数量与 generation/sequence |
| 配装计算 | `allocation.*` | 请求、求解、保存、失败和旧 generation 结果丢弃 |
| 角色 | `role.*` | 索引/详情、配置保存/重置、替换和 dirty 离开决策 |
| 基础权重 | `basic_weight.*` | 加载、保存、重置和 dirty 离开决策 |
| 公共额外形状 | `shape_bonus.*` | 公共覆盖保存和恢复发行默认 |
| 角色图纸 | `blueprint.*` | 生成、失败和旧账号结果丢弃 |
| 仓库 | `warehouse.*` | 固定快照加载（含驱动、空幕、已装备、锁定和角色实例数量）、规则评估、手工状态计划和 nte-core 写回 |
| 极速装配 | `equipment_apply.bulk_*` | 任务预检、执行、验证和最终摘要 |
| 视觉扫描 | `scanning.*` | 冻结依赖、扫描来源和生命周期 |
| 单件鉴定 | `identification.*` | 输入来源、连续截图和结果生命周期 |
| 战报 | `battle_report.*` | 采集生命周期、最终摘要持久化、历史恢复和页面状态保存；只记录记录 ID、状态、数量和安全错误，不记录原始摘要或伤害明细 |
| 环境 | `environment.*` | 游戏检测、nte-core/dwmapi 诊断、插件部署与恢复 |
| 更新 | `update.*` | 检查、下载请求、取消、失败、完成和安装器启动 |

支持 `started`、`succeeded`、`failed` 的阶段由 `operation_scope()` 自动附加 `phase` 和 `duration_ms`。取消、丢弃和降级事件使用独立名称，不伪装成成功。

战报事件允许的附加字段包括 `battle_record_id`、`persistence_status`、`retention_kind`、
`inserted`、`changed`、`pruned_record_count`、`character_count`、`skill_count` 和 `total_hits`。不得写入
`raw_summary_json`、角色 ID 列表、技能列表、完整伤害表或账号数据库路径。

## 会话文件

- 常驻：`accounts/<account_id>/logs/nte_runtime.log`，INFO 以上；
- 详细：`accounts/<account_id>/logs/nte_runtime_YYYYMMDD_HHMMSS[_N].log`，DEBUG 以上；
- 每次重新开启详细日志都创建新文件；
- 账号切换先结束旧账号会话，再在新账号目录按其设置创建新会话；
- 设置页“清空”只清空界面文本，不删除日志文件。

## 脱敏边界

禁止记录 Mirror CDK、Token、Cookie、Authorization、带鉴权查询参数的 URL、完整 nte-core RPC payload、OCR 全文、完整背包装备列表、账号显示名称、截图内容和可识别用户的绝对路径。

异常进入结构化日志前必须经过统一脱敏，只保留异常类型、安全消息和允许的错误码。相关自动测试入口为 `tests.test_observability_logging` 与 `tests.test_runtime_logging`。
