# 核心日志事件目录

事件名使用稳定英文点分格式，中文说明只用于阅读。日志记录业务阶段和安全摘要，不作为数据库事实源。

## 公共字段

| 字段 | 含义 |
| --- | --- |
| `event` | 稳定事件名 |
| `feature` | 功能域 |
| `operation_id` | 一次用户操作跨 Controller、worker、Service 和 Integration 的关联 ID |
| `account_id` | 内部账号 ID，不记录显示名 |
| `context_generation` | `AppContext` 账号代次 |
| `snapshot_id` | 冻结库存快照 |
| `slot_id` | 配装槽位；涉及方案保存、导入或装配时记录 |
| `job_id` | 持久任务 ID |
| `source` | 受控来源枚举，如 `nte_core`、`vision` |
| `phase` | `started`、`succeeded`、`failed` 或稳定业务阶段 |
| `duration_ms` | 阶段耗时 |
| `result` | 脱敏结果摘要 |

未适用字段不写入，不使用虚假空值。支持标准生命周期的操作通过 `operation_scope()` 自动附加 phase 和
duration；取消、过期丢弃、待确认和降级使用独立事件，不伪装为 succeeded。

## 事件族

| 功能 | 事件前缀/关键事件 | 主要排查内容 |
| --- | --- | --- |
| 应用 | `application.*` | 版本、启动、异常和退出 |
| 账号 | `account.switch_*` | 阻止、停止、generation 切换、重建和完成 |
| 迁移 | `database.*` | schema、公共形状迁移、事务失败和重试 |
| 同步 | `inventory_sync.*` | 连接、候选、稳定化、提交、运行时状态增量、保留策略和停止原因 |
| 扫描 | `scanning.*` | 冻结依赖、捕获驱动、分页、解析、提交与扫描后状态管理 |
| 计算 | `allocation.*` | 冻结请求、求解、目标槽位、保存、失败和过期丢弃 |
| 配装槽位 | `loadout_slot.*` | 创建、重命名、归档、当前方案切换与锁冲突 |
| 角色 | `role.*` | 索引/详情、配置、替换、动态权重与 dirty 决策 |
| 基础权重 | `basic_weight.*` | 账号权重、自建角色与底盘保存/重置 |
| 官方额外形状 | `shape_bonus.*` | 拒绝旧公共覆盖写入，以及旧覆盖清理/迁移诊断 |
| 图纸 | `blueprint.*` | 生成、失败和旧账号结果丢弃 |
| 仓库 | `warehouse.*` | 固定快照、运行时覆盖、筛选、计划、RPC、待确认和最终状态 |
| 鉴定 | `identification.*` | 输入来源、热键 owner、识别与展示生命周期 |
| 极速装配 | `equipment_apply.*` | 槽位预检、下发、完整快照/范围事件确认、重试和摘要 |
| 自动装配 | `drive_assembly.*` | 页面阶段、输入后端、动作、停止与可见结果 |
| 倒带 | `rewind.*` | 推荐请求、八槽保存、OCR 阶段、十连计划和停止 |
| 战报 | `battle_report.*` | capture 生命周期、摘要持久化、历史恢复和保留策略 |
| 环境 | `environment.*` | Npcap、nte-core、dwmapi、Mod Loader、VC Runtime、SDK 缓存、pipe、部署与恢复 |
| 更新 | `update.*` | 检查、下载、取消、失败、完成和安装器启动 |

同步与仓库允许记录驱动、卡带、已装备、锁定和角色实例的聚合数量，不记录 UID 列表。战报允许字段包括
`battle_record_id`、`persistence_status`、`retention_kind`、`inserted`、`changed`、
`pruned_record_count`、`character_count`、`skill_count` 和 `total_hits`；不记录原始摘要或伤害明细。

`inventory_sync.snapshot_commit_retry` 的 SQLite 保存失败诊断包括 `save_error_code`、`save_stage`、
`sqlite_exception_type`、`sqlite_errorcode`、`sqlite_errorname`、`sqlite_message` 和 `rollback_status`；
底层未提供的错误码或错误名不写入。`save_stage` 区分开启事务、写快照、写装备、写词条、更新装备角色映射、
更新独立角色映射、切换当前指针和提交。回滚失败时另记 `rollback_error` 的同类 SQLite 安全字段，保留首次
错误。分类使用 SQLite 主错误码，诊断保留完整扩展错误码。消息只允许固定 SQLite 文案与限定格式的表、列或
约束名；其他原始消息不公开，避免触发器或绑定错误夹带业务数据、路径。该诊断不读取或记录 SQL 参数、UID、
完整快照和账号显示名。

鼠标扫描报告相关事件只记录 profile、分辨率、预计/捕获数、页数、队列高水位、耗时和安全终止类型。
扫描后状态管理只记录计划/完成数量与状态迁移聚合，不记录目标索引。

## 会话文件

- 常驻：`accounts/<account_id>/logs/nte_runtime.log`，INFO 以上；
- 详细：`accounts/<account_id>/logs/nte_runtime_YYYYMMDD_HHMMSS[_N].log`，DEBUG 以上；
- 每次重新开启详细日志创建新文件；
- 账号切换先结束旧账号会话，再按新账号设置创建会话；
- 设置页“清空”只清空界面文本，不删除日志文件。

## 脱敏边界

日志不写 Mirror CDK、Token、Cookie、Authorization、鉴权查询参数、完整 nte-core RPC、完整背包、UID
列表、账号显示名、OCR 全文、截图内容、用户绝对路径、窗口标题和可复原业务 payload。

异常进入结构化日志前经过统一脱敏，只保留异常类型、安全消息和允许的错误码。自动测试入口为
`tests.test_observability_logging` 与 `tests.test_runtime_logging`。
