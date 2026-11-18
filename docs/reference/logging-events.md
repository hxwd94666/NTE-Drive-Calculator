# 核心日志事件目录

事件名使用稳定英文点分格式，中文说明只用于阅读。日志记录业务阶段和安全摘要，不作为数据库事实源。

GUI 入口在账号初始化和后台线程启动前关闭控制台日志输出，避免 Windows 控制台选择模式或阻塞管道暂停业务线程。账号常驻文件日志、用户开启的详细日志及界面日志仍按各自生命周期运行；命令行工具保留控制台输出。

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
| 静态发行读取 | `static_catalog.read_failed` | 只在底层读取失败时记录 pin、哈希、SQLite 打开/事务、绑定、元数据或退出复核阶段，以及有界异常链和 Windows/SQLite 原始错误码；不把通用“占用或读取失败”文案当成真实占用证据，不记录 SQL、业务输入或静态表正文。 |
| 鉴定 | `identification.*` | 输入来源、热键 owner、识别与展示生命周期 |
| 极速装配 | `equipment_apply.*` | 槽位预检、下发、完整快照/范围事件确认、重试和摘要；补救用 `command_retry`、`recovery_waiting`、`recovery_resumed`，步骤用 `command_accepted`、`command_failed`、`role_failed`。返回未完成结果记录 `bulk_failed`，不得仅因无异常而记录成功；摘要成功仅表示派发完成。 |
| 自动装配 | `drive_assembly.*` | 页面阶段、输入后端、动作、停止与可见结果 |
| 倒带 | `rewind.*` | 推荐请求、八槽保存、OCR 阶段、十连计划和停止 |
| 战报 | `battle_report.*` | capture 生命周期、摘要持久化、历史恢复和保留策略 |
| 环境 | `environment.*` | Npcap、nte-core、dwmapi、Mod Loader、VC Runtime、SDK 缓存、pipe、部署与恢复 |
| 手动环境检测 | `environment.detection_failed` | 固定原因、下一步、异常类型、失败 RPC 接口、已识别业务原因码、实际 Core SHA-256 和请求发出前已确认的握手事实；本地故障日志额外保留实际启动文件绝对路径。不写完整 RPC 或 stderr，后台轮询不重复记录。 |
| 更新 | `update.*` | 检查、下载、取消、失败、完成和安装器启动 |

`warehouse.rules_failed` 记录智能规则生成失败的阶段（上下文核对、首次评级或重新生成），以及最多六层异常的类型、受控错误码和来源位置；
保留 Win32 与 SQLite 数字错误码，以区分真实文件占用、权限及内部读取故障。取消不记录为该失败事件，不写规则草稿、匹配配置或装备正文。
静态读取单元内调用方抛出的异常保留原类型与原因，不统一改报静态文件占用；实际静态发行读取失败仍保留发行错误码。

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

- `native_sync.projection_read`（DEBUG）：状态、刷新、分页耗时与次数；失败时记录所在阶段、固定域错误码或异常类型，不写 RPC 错误正文。正常读取、尚未登录的等待、取消和可重试的未完成状态只进详细日志。
- `native_sync.refresh_finished`（DEBUG）：分别记录装备、角色和 `all_items` 刷新耗时、布尔就绪状态、条目数及固定白名单的未完成原因。新版 DLL 的可选 `collectionWork` 提供批次数、扫描/验证单位数、总历时、批内累计耗时、最长批次及角色关联装备详情读取次数；不含 UID、字段原文或返回正文，旧 DLL 未提供的计数不补零。总历时包含批间等待，批内耗时是墙钟观测，不等于 CPU 时间或游戏帧率。
- `native_sync.retry_deferred`（DEBUG）：同一来源修订未完成时的 1、2、4、8、10 秒重试等待；来源修订变化不沿用旧等待，不提交残缺集合。普通文件与界面不输出这些逐次重试。
- `native_sync.read_issue`（WARNING）：刷新结果中的固定身份／完整性错误码、数量／容量上限或截断，以及投影读取的协议、传输、映射与能力故障。`failed=true` 本身不认定为故障，容器暂不可用、来源变化、快照未完成、取消和可重试控制等待不告警。同一连接、域、scope 与阶段仅在故障签名变化时记录；完整成功后再次发生重新记录。去重不以修订、任务号、耗时或退避秒数为键，连接重建／账号上下文更换后重置，不抑制其他业务失败事件。只存固定码和阶段，无原始错误正文；日志策略不改变读取、重试、完整性判定、取消及保存行为。
- `native_sync.runtime_cost`：支持该字段的 DLL 返回游戏线程快照周期与读取批次的累计次数、累计微秒和最大单次微秒；最多每 30 秒记录变化。可选 `inventory_notifications` 仅保留通知类型（0–255）、累计次数、Items 条数、空数组/无效数组头次数、最后单调毫秒；拒绝负数、非法类型并丢弃额外字段，不记录成员、UID、物品数量值或正文。计数归 DLL 生命周期，互有包含关系，不相加、不当作游戏 FPS；旧 DLL 未提供时不补零。通知计数不证明增量事件完整。
- `native_sync.timing_summary`：可选 DLL `snapshot_diagnostics` 的分段累计计时，最多每 30 秒记录变化；仅接受 version=1、固定阶段名和有界非负整数。保留次数、总/最大微秒、超过 8333/20000 微秒次数，以及慢调用总数和缓冲区竞争丢弃数。阈值是诊断分桶，不是实测帧预算；嵌套阶段不能求和，缺失阶段不补零。
- `native_sync.hud_interaction` / `native_sync.hud_interaction_event`：可选 `hud_interaction` version=1 的 HUD 开关、角色读取、技能查询、HUD 绘制活动计数、捕获到的异常码及有界事件。沿现有状态读取观测，最多每秒接收一次；状态变化立即记录，累计摘要最多每 30 秒一次，不增加游戏轮询。最近事件最多 32 条，以序号和单调时间去重；拒绝未知阶段、未知事件、越界数字与额外字段。觉醒通知、HUD 开关和任意角色 E/Q 选择状态变化后 10 秒内，各阶段最多每秒采样一对开始/结束。技能选择只记录 E/Q、候选数和固定原因，不记录角色实例、技能类、对象地址或 UID。`active_mask` 的 control/character/skill_query/hud 位为 1/2/4/8，重叠可能来自嵌套调用，不能解释为线程竞争。环形覆盖、竞争丢弃和进程崩溃前尚未送达的记录均可能缺失；它不是崩溃转储，也不能由一条 begin 缺少 end 认定该函数崩溃。旧组件缺字段时不补零。
- `native_sync.slow_pulse`：同一低频观测最多接收最近 8 次加生命周期最慢一次的去重慢调用，保留固定域、all_items、回调序号、单调起点、临时采集 job/step 及分段耗时。回调序号与单调起点共同去重，允许 DLL 重启后序号从头开始；不记录 provider、对象、UID、路径或响应正文。缓冲区不可读或超界时不输出样本。它是有界留样，不是完整逐帧记录；`native_sync.refresh_finished` 可选 `diagnostic_job/diagnostic_step` 用于关联同一 DLL 会话内的刷新任务，旧组件缺字段时省略。
- `native_session.idle_closed`：自动同步与插件关闭、且无战报、库存租约或进行中读取时释放共享连接。
- `native_core.invalid_json`：响应字符数、完整换行标记、解析失败位置、Core 哈希及可获得的退出码；不记录响应片段或原始解析异常。
- `native_core.output`：仅消费 Core 固定诊断前缀与白名单字段，记录输出总字节、已写字节、耗时及 slow/timeout/failed；其他 stderr 不直接转入常驻日志。

`inventory_sync.furniture_save_failed` 仅记录真实账号写入失败的异常类型；同类连续故障不重复刷屏，至少十秒后重试，成功后允许记录新的故障。它不附家具数值或原始环境正文，不增加前端通知。正常家具保存与相同值复用不写逐次 INFO。

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

### 计算、仓库与战报的请求计时

普通日志只保留前台任务的一条 `*.timing_summary` 终态摘要：`operation_id`、任务返回/失败/取消、
总墙钟耗时、阶段数、最多三个包含式长阶段及三个失败阶段。`returned` 只表示任务返回，不证明游戏写入已确认。
后台正常读取、仓库分和快速卡片刷新不写普通摘要；中间边界不刷界面。

`allocation.timing_stage`、`warehouse.timing_stage`、`battle_report.timing_stage` 使用同一次操作的 `operation_id`，记录
`stage`、`boundary`、`sample_count`、累计 `duration_ms` 和单次 `max_duration_ms`。
只收聚合数值：角色／装备／偏好／图纸数量、图纸缓存命中与未命中、见证复算与复用次数、
修复轮数、储备分桶最大规模、复制字节及本次批量协议是否生效。不记角色名、规则名、路径、UID、
配置、请求或异常正文。取消和异常分别计入 `cancelled_count`、`error_count`，原异常继续传播。
`sample_count=0` 的数量说明没有对应耗时样本，不把缺项补成零毫秒。

| 阶段族 | 口径 |
| --- | --- |
| `input.*`、`settings.*` | 静态读取单元实际进入、冻结账号依赖、库存与锁、设置事务及队列；静态读取不含持有租约期间的全部计算。 |
| `allocation.*`、`comparison.*` | 图纸、库存转换、评分准备、基础分配、增强作业／后处理、旧槽位比较及来源复核。伤害整体作业包含冻结输入传输和组件执行，不冒称只有 Rust 搜索耗时。 |
| `allocation.damage.kernel.*` | 组件提供的输入核对、图纸、B0、后处理及结果核对；轴投影/重放/比较是后处理的子阶段，纯数字汇总不含事件、角色或伤害表。子阶段与父阶段不能相加。 |
| `blueprints.*` | 当前请求图纸缓存命中／未命中、缓存复制及真正发生的原生图纸调用；不因多次调用图纸适配器就推定发生多次原生求解。 |
| `predecessors.*`、`recommendation.*` | 前序手动／评分阶段、智能输入准备、对照与正式推荐；桶大小仅在已有遍历中统计，不为诊断再扫库存。 |
| `witness.*`、`loss.*` | 全角色、逆序、单角色、偏好原生／Python 分支，以及损失修复和复用。单角色及重复调用只聚合，不逐角色日志。 |
| `read.*`、`quality.*` | 仓库首次读取与独立后台仓库分各有独立请求，不能当成同一次智能推荐或配装求解。 |
| `delivery.*`、`gui.*` | worker 标记返回到 GUI 接收的投递等待、同步校验、状态投影、筛选排序、模型提交、结果渲染；保存确认与结果提示的人工等待单列，不当成求解耗时。 |
| `dispatch.*`、`save.*` | 派发前门禁、同步调用墙钟、明确未派发的退避、后验等待。`dispatch.call` 不含外层门禁／退避，但包含同步适配器开销，不等同于服务端纯执行时间。 |

`*.timing_stage` 和 `*.timing_boundary` 明细仅在既有详细日志启用时按 DEBUG 输出；未启用时不逐阶段格式化日志。
`*.timing_boundary` 与 `*.timing_summary` 的 `elapsed_ms` 从该请求创建开始计算，
覆盖后台接收、GUI 提交、owner 退场或首绘等边界。父子阶段可能互相包含，不能直接把所有行相加；
不同 `operation_id` 的冷／热任务、首次仓库分和保存任务也不能拼为一次总耗时。
既有 `dispatch_rpc_duration_ms` 含门禁与退避的历史口径不变，使用新增阶段分离原因。

后台只累积内存计数；身份仍有效的 owner 在中间边界提交详细记录，在明确终态 `finish` 提交一次摘要并关闭计时。
失败后关窗不再追加取消或成功摘要；旧任务晚到不复活已关闭计时。每次请求最多保留 96 个累计阶段、
每次待提交最多 96 个阶段和 32 个时间标记，不逐装备／逐派发刷日志。切换账号、关闭或新请求替代时撤销旧计时，
旧 worker 结果不写入新账号日志。日志 sink 故障不触发业务重试或覆盖业务异常。

任务失败保留 WARNING 摘要，原始业务错误事件和错误码不降级。记录到的单阶段累计耗时达到本机日志门槛时
保留慢请求摘要：卡片/筛选为 250ms、仓库读取为 2000ms、仓库分及其他任务为 10000ms；这些是日志筛选门槛，
不宣称预算超时或业务失败。仅总墙钟时间较长不触发慢告警，避免人工确认等待被误判；主动取消不升级慢告警，父子阶段不相加。

原生采集的 `native_sync.runtime_cost`、`native_sync.timing_summary`、`native_sync.slow_pulse` 与普通
HUD 状态/交互明细改为 DEBUG，不改变现有轮询、30 秒限频或去重。新增 HUD 故障计数和 fault 事件保留 WARNING；
同一低频批次中新观测到的至少 100ms 回调只保留一条 `native_sync.slow_pulse_summary` 告警，附样本数、
最大耗时和固定域，完整阶段仍在详细日志。100ms 仅为日志门槛，不改 DLL 的采样阈值，也不代表实测丢帧。

界面首绘通过页面所属的一次性观察器记录 `gui.first_paint_dispatch`，只证明 Qt 开始派发首个绘制事件，
不是屏幕呈现、整页绘制完成或 GPU 帧耗时。不强制刷新、不采截图、不打开隐藏页面；隐藏、被替代或三秒内未观测到绘制
分别记录 `view_hidden`、`paint_superseded`、`paint_unobserved`，不补零。
观察器在 GUI 回调内开始，最长三秒内以 50 ms 间隔估计事件循环额外延迟，聚合 `peak_lag_ms`，
首绘或超时即停止；不是新增常驻轮询。未取得采样时省略，既有全局慢心跳记录继续独立工作。

读取已有日志的汇总入口：

```powershell
python tools/quality/summarize_request_timings.py <本机运行日志路径>
python tools/quality/summarize_request_timings.py <本机运行日志路径> --request-id <operation_id> --json
```

汇总只输出上述请求标识、稳定阶段和数值，不回显其他日志正文。旧日志缺新事件时明确报告无样本。
新增计时不代表已经完成真实性能对照；冷／热基准、UI 专项和模拟写回须按当轮测试授权分别执行。

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

结构化日志入口仍按原规则脱敏路径；本地日志 sink 对普通消息中的常见凭据执行统一遮盖，保留文件路径。共享脱敏入口同时移除嵌入 URL 的用户凭据，并遮盖完整查询及片段；合法原始引号和百分号编码不截断凭据遮盖范围。结构化字段按正式字段名阻止完整 RPC／payload、UID 列表、账号显示名及 OCR／截图内容进入记录。此兜底不代替调用方的白名单摘要责任，也不保证任意自由文本都可公开。计算与目录加载异常只记录有界消息和最多八个堆栈位置，不写原始整段 traceback 或局部变量。其他遗留直接日志仍可能包含业务值，后续收口见[路线图](../roadmap.md#6-界面文本与日志)。自动测试入口为
`tests.observability.test_observability_logging` 与 `tests.observability.test_runtime_logging`。
