# 核心日志事件目录

事件名使用稳定英文点分格式，中文说明只用于阅读。日志记录业务阶段和安全摘要，不作为数据库事实源。

`inventory_sync.stopped` 记录 `stop_stage` 与受控 `stop_reason`：显式停止请求、账号上下文变化、权限撤销、战报让出、维护、明确连接中断、原生会话取消或其他操作取消。`connection_lost` 同时进入同步展示状态，供有限自动恢复使用；笼统的 `native_session_cancelled` 不授权自动恢复。同步异常退出也记录停止原因，不从异常文本推断恢复资格。

`native_session.inspection_failed` 记录检测异常类型、RPC 数字错误码、白名单原因和 `connection_retained`。`REQUEST_IN_PROGRESS` 等明确可重试状态保留共享连接；协议或传输故障仍收尾。禁止记录完整 RPC 错误数据或任意服务端错误文本。
原生会话取消仅说明取消来自会话链路，不能据此推断是用户点击、游戏退出或组件故障；不会记录任意取消异常正文。

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
| 页面性能 | `PERF allocation.catalog_read`、`PERF allocation.catalog_validate`、`PERF allocation.catalog_apply`、`PERF basic_weight.form_apply`、`PERF home.summary_apply`、`PERF ui.event_loop_lag`、`PERF equipment.render` | 分开记录后台读取/校验与主线程应用。事件循环每 250 ms 采样，仅额外延迟达到 250 ms 时记录，日志间隔至少 5 秒；只记耗时、页面 key 和是否复用，不记角色名、路径或方案内容。 |
| 配装槽位 | `loadout_slot.*` | 创建、重命名、归档、当前方案切换与锁冲突 |
| 角色 | `role.*` | 索引/详情、配置、替换、动态权重与 dirty 决策 |
| 基础权重 | `basic_weight.*` | 账号权重、自建角色与底盘保存/重置 |
| 官方额外形状 | `shape_bonus.*` | 拒绝旧公共覆盖写入，以及旧覆盖清理/迁移诊断 |
| 图纸 | `blueprint.*` | 生成、失败和旧账号结果丢弃 |
| 仓库 | `warehouse.*` | 固定快照、运行时覆盖、筛选、计划、RPC、待确认和最终状态 |
| 鉴定 | `identification.*` | 输入来源、热键 owner、识别与展示生命周期 |
| 极速装配 | `equipment_apply.*` | 槽位预检、下发、完整快照/范围事件确认、重试和摘要；补救用 `command_retry`、`recovery_waiting`、`recovery_resumed`，步骤用 `command_accepted`、`command_failed`、`role_failed`。返回未完成结果记录 `bulk_failed`，不得仅因无异常而记录成功；摘要成功仅表示派发完成。 |
| 自动装配 | `drive_assembly.*` | 页面阶段、输入后端、动作、停止与可见结果 |
| 倒带 | `rewind.*` | 推荐请求、八槽保存、OCR 阶段、十连计划和停止 |
| 战报 | `battle_report.*` | capture 生命周期、摘要持久化、历史恢复和保留策略 |
| 环境 | `environment.*` | Npcap、nte-core、dwmapi、Mod Loader、VC Runtime、SDK 缓存、pipe、部署与恢复 |
| 手动环境检测 | `environment.detection_failed` | 固定原因、下一步、异常类型、失败 RPC 接口、已识别业务原因码、实际 Core SHA-256 和请求发出前已确认的握手事实；本地故障日志额外保留实际启动文件绝对路径。不写完整 RPC 或 stderr，后台轮询不重复记录。 |
| 更新 | `update.*` | 检查、下载、取消、失败、完成和安装器启动 |

同步与仓库允许记录驱动、卡带、已装备、锁定和角色实例的聚合数量，不记录 UID 列表。战报允许字段包括
`battle_record_id`、`persistence_status`、`retention_kind`、`inserted`、`changed`、
`pruned_record_count`、`character_count`、`skill_count` 和 `total_hits`；不记录原始摘要或伤害明细。

背包同步的常驻 INFO 日志记录以下判定链，不要求预先开启详细日志：

| 事件 | 诊断内容 |
| --- | --- |
| `inventory_sync.baseline_selected` | 本次去重基线是否有效、来源、件数和稳定等待时长；视觉来源不作为原生基线。 |
| `inventory_sync.baseline_invalid` | 已保存快照无法初始化去重基线；仍可接收新的完整快照，不记录原始校验异常。 |
| `inventory_sync.core_connected` | Core/数据版本（握手提供且格式有效时）、协议与能力；不记录可执行文件绝对路径。 |
| `inventory_sync.event_evaluated` | 已取出事件的稳定化判定、固定原因码、完整标记、字段类型、声明/实际数量、代次/序号及装配守卫件数。 |
| `inventory_sync.session_summary` | 累计处理数、各判定/拒绝原因计数、提交/保存失败数、待稳定件数、距上次处理事件的时长；停止与异常均输出最终摘要。 |
| `inventory_sync.snapshot_committed` | 新快照摘要、上一份库存来源和件数、内容稳定时长；数量缩减只记录事实，不凭历史数量认定残缺。 |
| `inventory_sync.failed` | 保留域错误码，并用 `failure_stage` 区分设置加载、Core 启动、抓包启动、事件处理、保存及提交后处理阶段。 |

`event_evaluated` 按“判定结果＋固定原因码”限频：首次立即记录，随后同类最多每 30 秒一次；汇总计数不丢弃。
会话摘要每 30 秒及退出时记录。处理计数是工作线程从最新事件槽取出并判定的次数，不等于网络包数、Core
总发出事件数或合并前回调次数；零次只表示本会话尚未处理库存事件，不能单独证明网卡没有流量。
拒绝原因码区分不完整快照、声明数量不符、重复装备/角色实例、其他结构无效、装配库存守卫不匹配、
过期/重复序号，以及角色列表升级期间忽略旧格式事件。日志不记录可含 UID 的原始校验异常文本。

原生同步与通信另记录以下脱敏诊断，不要求保存完整原始采集文件：

- `native_sync.projection_read`：状态、刷新、分页耗时与次数；失败时记录所在阶段、固定域错误码或异常类型，不写 RPC 错误正文。
- `native_sync.refresh_finished`：分别记录装备、角色和 `all_items` 刷新耗时、布尔就绪状态、条目数及固定白名单的未完成原因。新版 DLL 的可选 `collectionWork` 提供批次数、扫描/验证单位数、总历时、批内累计耗时、最长批次及角色关联装备详情读取次数；不含 UID、字段原文或返回正文，旧 DLL 未提供的计数不补零。总历时包含批间等待，批内耗时是墙钟观测，不等于 CPU 时间或游戏帧率。
- `native_sync.retry_deferred`：同一来源修订未完成时的 1、2、4、8、10 秒重试等待；来源修订变化不沿用旧等待，不提交残缺集合。
- `native_sync.runtime_cost`：支持该字段的 DLL 返回游戏线程快照周期与读取批次的累计次数、累计微秒和最大单次微秒；最多每 30 秒记录变化。可选 `inventory_notifications` 仅保留通知类型（0–255）、累计次数、Items 条数、空数组/无效数组头次数、最后单调毫秒；拒绝负数、非法类型并丢弃额外字段，不记录成员、UID、物品数量值或正文。计数归 DLL 生命周期，互有包含关系，不相加、不当作游戏 FPS；旧 DLL 未提供时不补零。通知计数不证明增量事件完整。
- `native_sync.timing_summary`：可选 DLL `snapshot_diagnostics` 的分段累计计时，最多每 30 秒记录变化；仅接受 version=1、固定阶段名和有界非负整数。保留次数、总/最大微秒、超过 8333/20000 微秒次数，以及慢调用总数和缓冲区竞争丢弃数。阈值是诊断分桶，不是实测帧预算；嵌套阶段不能求和，缺失阶段不补零。
- `native_sync.hud_interaction` / `native_sync.hud_interaction_event`：可选 `hud_interaction` version=1 的 HUD 开关、角色读取、技能查询、HUD 绘制活动计数、捕获到的异常码及有界事件。沿现有状态读取观测，最多每秒接收一次；状态变化立即记录，累计摘要最多每 30 秒一次，不增加游戏轮询。最近事件最多 32 条，以序号和单调时间去重；拒绝未知阶段、未知事件、越界数字与额外字段。觉醒通知、HUD 开关和任意角色 E/Q 选择状态变化后 10 秒内，各阶段最多每秒采样一对开始/结束。技能选择只记录 E/Q、候选数和固定原因，不记录角色实例、技能类、对象地址或 UID。`active_mask` 的 control/character/skill_query/hud 位为 1/2/4/8，重叠可能来自嵌套调用，不能解释为线程竞争。环形覆盖、竞争丢弃和进程崩溃前尚未送达的记录均可能缺失；它不是崩溃转储，也不能由一条 begin 缺少 end 认定该函数崩溃。旧组件缺字段时不补零。
- `native_sync.slow_pulse`：同一低频观测最多接收最近 8 次加生命周期最慢一次的去重慢调用，保留固定域、all_items、回调序号、单调起点、临时采集 job/step 及分段耗时。回调序号与单调起点共同去重，允许 DLL 重启后序号从头开始；不记录 provider、对象、UID、路径或响应正文。缓冲区不可读或超界时不输出样本。它是有界留样，不是完整逐帧记录；`native_sync.refresh_finished` 可选 `diagnostic_job/diagnostic_step` 用于关联同一 DLL 会话内的刷新任务，旧组件缺字段时省略。
- `native_session.idle_closed`：自动同步与插件关闭、且无战报、库存租约或进行中读取时释放共享连接。
- `native_core.invalid_json`：响应字符数、完整换行标记、解析失败位置、Core 哈希及可获得的退出码；不记录响应片段或原始解析异常。
- `native_core.output`：仅消费 Core 固定诊断前缀与白名单字段，记录输出总字节、已写字节、耗时及 slow/timeout/failed；其他 stderr 不直接转入常驻日志。

`inventory_sync.snapshot_commit_retry` 还记录本会话保存尝试次数与候选件数。SQLite 保存失败诊断包括 `save_error_code`、`save_stage`、
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

## 性能排错记录

性能记录使用 `logs/performance/performance_<时间>_<会话>.jsonl`，格式 `calc.performance/2`，
指标身份 `calc.performance.metrics/1`。指标口径、来源、开关组合、覆盖范围与生命周期只在
[性能指标契约](performance-metrics.md)维护，不在各层另定义近似公式。

后台约每半秒读取；服务耗时继续来自 `native_status.runtimePerformance` version=1 的白名单计数，
帧指标来自同一游戏进程的 PresentMon 呈现间隔，组件耗时携带 `cost_coverage` 与 `cost_complete`。
日志仅保存这些统计、UTC、单调时间、会话 ID 和采样错误状态；原始 CSV、完整 RPC、游戏对象、伤害和 UID 不写入。
未采到的值为 null，不把缺失当作零，也不拿 HUD 回调间隔冒充呈现帧时间。

常驻日志的 `performance.started/stopped` 使用相同会话 ID。性能目录与 `nte_core/raw_capture/` 相邻，
可按 UTC 对照；原始采集文件不携带此性能会话 ID，不能声称已完成逐事件精确关联。
正常结束写 `session_end`；缺少尾记录按部分记录处理。单会话上限 16 MiB，写入失败或达到上限停止该次记录，
实时显示继续；不自动删除历史日志。账号切换先结束旧文件，不将晚到样本写入新账号。

日志与原始采集互相独立：有性能日志不证明原始战报采集成功；关闭悬浮窗也不取消已开启的排错记录。
日志不进入默认账号导出或发行包。

## 脱敏边界

日志不写 Mirror CDK、Token、Cookie、Authorization、鉴权查询参数、完整 nte-core RPC、完整背包、UID
列表、账号显示名、OCR 全文、截图内容、窗口标题和可复原业务 payload。

本地故障日志允许保留与故障定位直接相关的文件绝对路径，不对其作路径脱敏；日志不进入默认账号导出或发行物。
手动检测的复制文本提供失败接口、业务原因码与组件哈希，实际启动路径只附加到本地故障日志。
部分业务读取失败时，检测可以保留本次请求发出前已确认的握手事实，但未完成的业务快照与就绪状态仍为未知。

结构化日志入口仍按原规则脱敏路径；本地日志 sink 对普通消息中的常见凭据执行统一遮盖，保留文件路径。共享脱敏入口同时移除嵌入 URL 的用户凭据，并遮盖完整查询及片段；合法原始引号和百分号编码不截断凭据遮盖范围。结构化字段按正式字段名阻止完整 RPC／payload、UID 列表、账号显示名及 OCR／截图内容进入记录。此兜底不代替调用方的白名单摘要责任，也不保证任意自由文本都可公开。计算与目录加载异常只记录有界消息和最多八个堆栈位置，不写原始整段 traceback 或局部变量。其他遗留直接日志仍可能包含业务值，后续收口见[路线图](../roadmap.md#6-遗留日志入口收口)。自动测试入口为
`tests.test_observability_logging` 与 `tests.test_runtime_logging`。
