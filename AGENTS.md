# NTE Drive Calc 仓库开发契约

本文件约束整个仓库，面向维护者与编码代理。它只描述当前仍有效的产品契约、工程门禁和迁移方向；
历史过程、版本流水账、临时排查结论和未落地设想不写入本文。技术文档统一从
[`docs/README.md`](docs/README.md) 进入，局部目录如需更严格规则，应增加更窄的 `AGENTS.md` 或
`AGENTS.override.md`。

本文必须保持可公开：不得写入账号、Token、CDK、私有地址、真实 UID、用户绝对路径、完整抓包、OCR
全文或未脱敏日志。

## 1. 规则等级与事实源

### 1.1 规则等级

1. **产品硬契约**：数据所有权、账号隔离、不可变快照、正式 ID/UID、评分与候选规则、配装槽位、保存、
   写回和装配确认语义。变更前先修改公共行为测试与相应文档，再修改实现。
2. **工程硬门禁**：静态库只读、迁移只追加、日志脱敏、依赖方向、版本与依赖唯一来源、仓库卫生、
   基础静态检查。新代码不得增加例外。
3. **目标架构**：UI → Controller → Service → Domain/Optimizer 与 DAO/Integration。新功能和实质性
   重写必须按此落地；存量兼容入口只允许收缩。

### 1.2 事实源优先级

发生冲突时按以下顺序处理：

1. 已通过的公共行为测试与数据库约束；
2. 本文件中的产品硬契约；
3. `docs/architecture.md`、`docs/features.md`、`docs/integrations.md` 与 `docs/reference/`；
4. 公开 Service/DAO/Integration contract；
5. UI 当前表现和存量兼容实现。

未实现事项只写入 `docs/roadmap.md`，不得表述为当前能力。界面文案、颜色和间距由代码与测试表达，
不升级为长期架构规则。

## 2. 修改前必须回答的问题

开始编码前，在任务说明、计划或测试命名中明确以下问题：

1. 输入属于发行静态、本机共享、应用全局还是当前账号？
2. 操作冻结哪个账号、`AppContext.generation`、用户库绝对路径、`snapshot_id`、静态数据集、profile/配置
   版本、配装 `slot_id` 和输出目录？
3. 输出只返回内存结果，还是写入静态库、共享库、账号库、应用配置或外部 Integration？
4. Page/View、Controller、Service、Domain/Optimizer、DAO/Integration 分别拥有何种状态？
5. worker 如何取消？旧 token、旧 generation、旧账号路径、旧快照和旧槽位结果如何静默丢弃？
6. 哪些公共行为测试证明上游输入、下游保存、失败回滚和兼容入口均未破坏？

涉及真实游戏输入、插件、驱动或更新时，还要明确：操作前观测、提交动作、成功确认、超时状态、重试
上限和人工验收证据分别是什么。

## 3. 交付方式

### 3.1 产品能力或语义变化

```text
固定公共行为 → 更新公开 contract 与文档 → Domain/Service → DAO/Integration
→ Controller/UI → 迁移全部调用方 → 删除旧入口 → 专项测试 → core/full 与静态检查
```

- 产品规则变化不得伪装成重构、兼容或性能优化。
- 新旧语义不得长期双写、双读或由 UI 条件分流；最小完整切片结束时只保留一个公开入口。
- schema、payload、错误码或外部协议变化必须包含旧数据升级与失败重试测试。

### 3.2 缺陷修复

先用公共行为测试固定现状或重现缺陷，再做最小业务范围修复。若触及存量例外，可维护原路径，但不得
复制例外或顺带展开跨层迁移。

### 3.3 架构迁移

架构迁移作为独立任务：先列出入口、调用方、状态所有者、兼容期和退出条件，再完成从公开 contract 到
调用方的最小闭环。纯迁移不得同时改变评分、OCR、伤害、配装、倒带或数据库语义。

### 3.4 完成定义

一次变更完成时应同时满足：

- 公开行为、数据所有权与生命周期已由测试表达；
- 没有遗留新的兼容门面、动态注入、跨 feature 私有调用或 TODO 双入口；
- 迁移可从新库创建、旧库升级、失败回滚和重试；
- 外部副作用具有操作前基线、提交记录和最终确认；
- 文档只保留更新后的当前事实；
- 专项测试、静态检查和要求的 core/full 结果逐项报告，存量失败与新增失败分开列明。

## 4. 当前技术基线与仓库结构

- 运行平台：Windows 10/11，Python 3.11，PySide6。
- 依赖声明唯一来源：`pyproject.toml`；锁文件：`uv.lock`。
- 应用版本唯一来源：`src/app/version.py::__version__`。
- GUI 组合根：`src/ui/app.py`；入口：`main.py`。
- 当前数据库基线：发行静态 schema v16、本机共享 schema v2、账号 schema v21。提升版本时必须在同一
  变更中更新常量、迁移、测试和本文。
- 质量入口：`tools/quality/run_tests.py`；`core` 是边界和关键行为集合，`full` 是全量 unittest 发现。

主要目录职责：

| 路径 | 职责 |
| --- | --- |
| `src/app` | 路径、账号上下文、应用级生命周期与版本 |
| `src/features` | 页面、View、Controller 和 feature 组合代码 |
| `src/services` | Qt 无关的应用编排与公开 contract |
| `src/domain`、`src/optimizer`、`src/solver` | 规则、评分、纯计算与求解 |
| `src/storage/sqlite` | SQLite schema、迁移、DAO 与事务 |
| `src/integrations`、`src/scanner` | nte-core、OCR、鼠标/手柄、外部进程和文件格式 |
| `src/observability` | 脱敏日志与操作追踪基础设施 |
| `assets`、`config/templates` | 随发行包提供的静态 UI/OCR 资源 |
| `data` | 发行静态库、公共共享基线与 manifest |
| `third_party` | 经审计的发行二进制、来源、版本与许可说明 |
| `tests` | 公共行为、边界、迁移、打包与真实环境验证器测试 |
| `tools` | 静态数据、质量、同步和 Windows 验证工具 |

`accounts/`、日志、扫描图片、临时 OCR、抓包、构建产物、安装包输出、WAL/SHM、本机 SDK/转储与开发
环境均为本机状态，不进入 Git。

## 5. 分层、依赖与组合根

目标依赖方向：

```text
UI Page / View
      ↓
Controller + immutable dependencies
      ↓
Application Service
   ↙                 ↘
Domain / Optimizer       DAO / Integration
                              ↓
                    SQLite / nte-core / 文件 / OCR / 游戏输入
```

### 5.1 各层职责

- **UI Page/View**：采集输入、投影结果、保存可丢弃交互状态；不写 SQL、不解析协议、不持有算法和其他
  页面控件。
- **Controller**：拥有一次操作的 worker、取消、token、忙碌态和 UI 投影；只依赖窄 Service/Integration，
  不代理其他 Controller。
- **Service**：冻结请求、编排规则与事务、返回 Qt 无关结果；不查找 MainWindow 或当前页面。
- **Domain/Optimizer/Solver**：接收完整不可变输入并纯计算；不访问 SQLite、Qt、日志、当前账号或外部进程。
- **DAO**：独占 SQL、schema、迁移和事务边界。
- **Integration**：独占协议、进程、驱动、OCR、鼠标/手柄输入和外部文件格式；不决定业务评分。
- **Observability**：提供脱敏事件与 sink，不依赖 feature、Service、DAO 或 UI。

公共 `EquipmentPresentation` 与 `GlobalHotkeyManager` 只在 `src.ui.app` 创建并显式注入。新页面通过
`AppContext` 或窄 dependencies 对象获取能力，不读取 `src.app.runtime` 全局路径，不以页面索引、
MainWindow 字段扫描或 Controller 服务定位器取依赖。

跨层关系使用官方 `character_id`、`item_id`、`suit_id`、`shape_id`、`property_id`、装备正式 UID 与
配装 `slot_id`。中文名和 `slot_name` 只用于展示。

### 5.2 当前兼容边界

以下是当前需要收缩的兼容边界，不是新代码范例：

- `src.optimizer.scoring.ScoringEngine` 在未注入评分目录时仍直接读取账号与静态 SQLite；新调用应由
  Service 构造不可变评分输入并注入，调用方迁完后删除回退。
- `src.features.inventory.page` 仍动态导出旧 MainWindow 方法；只维持现有 `__all__`，不得增加导出。
- `src.storage.sqlite.user_data_dao.UserDataDao` 是多个聚焦 DAO mixin 的兼容门面。调用方可维持已有公开
  surface；新业务优先使用窄 DAO/Service，不把新领域继续堆入门面。
- 计算 runner、配装、加权配置、扫描管理和部分 Page/Controller 仍直接打开 DAO。局部修复可沿原路径，
  新能力必须先建立窄 Service，再由 Controller 调用。
- 导航 mixin 依据 `NAV_ITEMS` 为 MainWindow 绑定按钮属性是限定的组合根行为；feature 不得仿照该方式
  动态注入 MainWindow 方法或业务字段。

例外处理原则只有三条：允许维护、禁止复制；迁移与业务语义分开；退出条件满足后删除例外，不追加
历史说明。

## 6. AppContext、账号与异步生命周期

`src.app.context` 是运行时路径和账号状态的唯一组合根：

- `ApplicationPaths`：应用根、资源、发行静态库、共享库和全局配置路径；
- `AccountContext`：账号 ID、用户库、配置、截图和日志目录；
- `AppContext.generation`：账号上下文代次；
- `AccountLifecycle`：账号切换时后台能力的停止、重建和恢复接口。

长任务创建时必须冻结：账号 ID、用户库绝对路径、generation、`snapshot_id`、静态 dataset/manifest、
profile/配置版本、配装 `slot_id` 集合、锁快照、token 和输出目录。回调或写入前重新核对所有相关值；
任一值过期时静默丢弃结果，不展示到新账号，也不写入新账号或新槽位。

账号切换顺序：

```text
检查并停止账号后台任务 → 切换 AccountContext → generation + 1
→ 重建窄服务 → 页面清除账号缓存 → 恢复允许自动运行的服务
```

- worker 初始化前和释放后都可能为 `None`；先复制局部引用，再判断
  `worker is not None and worker.isRunning()`。
- 取消只使本次 token 失效；后台结果到达时仍执行代次与路径复核。
- 应用退出走每个 feature 的公开 `close/stop` 入口，不直接修改 worker 内部状态。
- 背包同步、战报捕获、扫描、鉴定和游戏输入必须显式协调独占资源；不得依靠按钮禁用作为并发防线。

## 7. 数据所有权与 SQLite

| 数据域 | 路径 | 内容 | 写入者 |
| --- | --- | --- | --- |
| 发行静态 | `data/game_static.sqlite3` | 官方角色、装备、套装、形状、属性、成长、技能、敌人、推荐权重、毕业模板和来源 | 仅 `tools/game_data` |
| 公共共享 | `data/app_shared.sqlite3` | 官方角色额外形状标签与加成的发行基线/跨账号公共覆盖 | `SharedDataDao` 与发行基线工具 |
| 应用全局 | `config/global_ui_preferences.json` | 当前主题 | `GlobalThemeSettingsService` |
| 账号私有 | `accounts/<account_id>/user_data.sqlite3` | 快照、角色实例、权重、偏好、自建角色、配装槽位、方案、锁、任务和战报 | `UserDataDao` 与应用 Service |

### 7.1 读取与写入规则

- 读取优先级：账号显式配置 → 允许共享的公共覆盖 → 发行默认。
- 官方角色额外形状：公共覆盖 → 发行默认。
- 自建角色名称、权重、额外形状、默认套装与 5×5 底盘只属于当前账号。
- 角色基础权重、计算偏好、倒带偏好、配装、锁和任务只属于当前账号。
- 主题属于应用全局；账号切换不重新加载账号主题。旧账号主题只允许首次迁移一次。
- 发行包可把审核后的 `app_shared.sqlite3` 作为公共基线安装到数据根；更新基线是明确的发行数据变更，
  不把用户运行时数据库或本机临时修改当作发行输入。

### 7.2 静态数据

运行时只读 `game_static.sqlite3`。重新生成、同步或替换时，以确认后的数据库为权威，并在同一变更更新
`data/manifest.json` 的 dataset、schema、importer 和 SHA-256。至少运行
`tests.test_static_data_manifest`、`tests.test_static_game_database` 与相关 catalog 测试；合并或发布前运行
full。静态库和 manifest 必须作为一个原子变更审查。

### 7.3 账号迁移

账号迁移只追加；已发布 SQL 文件不得改名、重排或修改含义。schema 变化必须验证：

1. 新库从 v1 创建到最新版本；
2. 各受影响旧版本升级；
3. 事务失败完整回滚；
4. 修复后可重试；
5. 外键、唯一索引、视图和兼容读取均成立。

页面和 Service 不拼 SQL。DAO 保存事务是唯一最终一致性防线。

### 7.4 当前关键持久对象

- **库存快照**：不可变 `snapshot_id`、来源、完整性、装备、词条和角色实例；当前指针只指向完整稳定快照。
- **运行时状态增量**：账号 schema v21 只覆盖固定完整快照中已知正式 UID 的锁定、弃置、装备状态；
  残缺事件不得新增装备、替换库存集合或推进当前快照指针。
- **角色配装槽位**：每个角色拥有稳定 `slot_id`；`primary` 是兼容默认槽位，`slot_name` 仅展示。
  `role_loadout_slot.current_plan_id` 与活动方案必须事务一致。
- **活动方案**：保存角色、槽位、`source_snapshot_id`、assignment、payload、来源类型和锁状态。
- **方案 payload**：`assignment_scores` 保存逐件评分；`tape_main_values` 保存卡带满级主词条具体值。
- **计算保留锁**：约束后续求解与保存，不是游戏装备锁。
- **自建角色**：账号内正式自建 ID、权重、额外形状、默认计算套装与 5×5 底盘；底盘固定 20 个启用格位，
  单格可锁定。
- **账号设置副本**：结构化偏好按 key 复制读写；倒带使用 `rewind_recommendation`。
- **装配/状态任务**：保存冻结来源快照、配装槽位、角色项、事件、尝试次数和最终确认状态。

快照清理必须保护当前快照、所有活动方案及其槽位、锁定方案和未完成任务引用的来源快照，保证历史
方案仍可复现。

## 8. 不可变库存与来源能力

```text
nte-core 完整事件 ─┐
                    ├→ 完整性/稳定化 → 不可变快照 → 下游固定 snapshot_id
视觉/手柄完整扫描 ─┘

nte-core 状态增量 → 仅覆盖固定完整快照中的已知 UID 状态
```

- `InventorySnapshotStabilizer` 使用完整内容指纹和安静窗口判断稳定，不使用历史最大数量或玩家特定数量。
- `inventory.get_latest` 只读取最近捕获，不代表强制刷新游戏。
- nte-core 来源提供正式 UID、角色实例、可靠装备状态、仓库 RPC 与极速装配能力。
- `vision`/旧 `gamepad` 来源提供分析用临时 UID，可用于仓库、计算、历史展示、倒带库存和游戏界面自动
  装配；不进入极速装配，也不提供可靠角色装备归属。
- 来源能力只能通过 `inventory_source_capabilities` 等公开 helper 判断，不能从相同表结构、非空 UID 或
  UI 来源名称推断。
- 下游在操作开始前解析一次当前快照，运行中不追随最新指针。

视觉扫描开启“扫描后管理”时，写回能力只属于同一次冻结扫描会话：依据扫描索引倒序定位，逐件在游戏
详情面板复核操作前状态和操作后结果。它不把视觉临时 UID 升级为后续仓库可写 UID。

## 9. 计算、角色配置与评分

计算冻结账号、generation、快照、静态数据集、profile、角色顺序/平级组、目标配装槽位、锁快照和全部
角色配置，返回不可变 `WeightedAllocationPreview`。保存只消费该 preview，不重新打开最新状态补齐。

### 9.1 候选与硬约束

1. `AllocationLockSnapshot` 在候选构造前剔除全部锁定真实 UID；失效锁阻止计算。
2. 驱动副词条黑名单最先执行且为硬过滤；副词条自选不是硬过滤。顺序模式优先最深嵌套前缀，一致模式
   优先命中数量最多的候选池；组合无解时逐层放宽，最后回到完整候选池。
3. 卡带先执行套装与主词条硬过滤，再使用同一副词条分层回退；主词条优先于副词条。
4. 默认套装为四件套，只有玩家显式修改才覆盖。
5. “不限制评分等级”只取消副词条自选的评分门槛，不取消套装、卡带主词条和副词条黑名单。
6. 平级组暴击恢复顺序固定为：只换卡带 → 冻结必需套装件并重选额外件 → 仅失败角色从零重配；
   已成功同级角色不得在最后阶段释放。
7. 前序角色已分配的真实 UID 不再进入后续候选；虚拟占位评分为 0，不可锁定或极速装配。
8. 角色拖拽跨过 `>>` 批次边界时，每个被跨过的边界反向移动一格；后往前拖拽产生新边界后，将上一
   `>>`（没有则从首项）到新边界前的关系归并为 `=`。

### 9.2 默认配置与自建角色

- 未显式覆盖时，官方角色使用静态毕业模板中的默认套装与专武。
- 所有角色默认卡带主词条和副词条优先级均为未选择；升级不得覆盖账号已有显式配置，包括主角「零」
  已保存的“环合强度”配置。
- 有效暴击率上限按 `100 - 满级默认专武暴击率` 生成。
- 公开有效配置读取器负责合并模板默认和账号覆盖；全局最优只接受显式全局覆盖。
- 自建角色可参与视觉库存计算和游戏界面自动装配；不进入官方角色详情、nte-core 背包角色实例、游戏
  配装导入或极速装配。

### 9.3 权重与卡带值

- 账号基础权重是持久计算输入；未编辑账号使用发行推荐，编辑只影响当前账号。
- 角色详情的动态最终权重来自当前面板直伤边际归一化；未进入直伤公式的属性保留基础权重。动态权重
  只用于分析和替换排序，不写数据库，也不覆盖基础权重。
- 计算忽略快照中的一级卡带值，统一使用 `StatCatalog` 满级值：金/橙 `1.0`、紫 `0.8`、蓝 `0.6`。
- 新方案必须写 `payload.tape_main_values` 与 `payload.assignment_scores`；下游优先读取保存值，只有旧
  方案缺字段时调用同一 helper 回退。

保存前复核账号、generation、快照、profile、目标 `slot_id` 和锁；单角色、批量和加权保存必须共用同一
槽位 contract，不以角色名覆盖另一个槽位。

## 10. 配装槽位、方案与锁

- 一个角色可有多个配装槽位；默认 `primary` 槽用于旧数据兼容，新增、重命名、归档和选择均通过
  `LoadoutSlotSelectionService`/DAO 公开接口。
- 活动方案始终按自己的 `source_snapshot_id` 解析装备，不因库存更新而改查最新快照。
- 游戏配装视图是 nte-core 稳定快照的只读投影；玩家显式导入后才保存为
  `game-observed-loadout-v1`，并冻结来源快照、目标槽位、逐件评分和卡带满级值。
- 完整驱动但缺卡带的方案可保存为 `incomplete/missing_tape`；视觉来源不作为游戏配装导入来源。
- 计算方案与导入方案共同参与锁、删除、替换、装配和倒带推荐。

锁规则：

- 锁属于当前账号的活动方案/槽位，不调用游戏锁 RPC；
- 至少一个真实装备即可锁定，允许缺卡带；空方案或含虚拟占位的方案不可锁；
- 锁定方案不得删除、覆盖、归档当前槽位或单件替换；批量清空跳过并报告；
- 其他角色和槽位不得借用锁定 UID；DAO 保存事务再次检查冲突；
- 批量选择多个槽位时，`slot_id` 不重复、角色身份一致、来源为原生快照且装备 UID 全局不冲突。

`EquipmentPresentation` 是唯一公共装备展示组件，生成卡片、评级、属性收益、差异和结果区域；它不写
SQLite、不选择当前快照、不启动 worker。计算、配装、仓库和鉴定只通过公开接口复用。

## 11. 仓库、扫描与鉴定

### 11.1 仓库

仓库读取固定快照。`WarehouseInventoryService` 生成投影；状态管理 Service 生成并复核锁定/弃置计划；
Integration 执行动作。计划固定 `snapshot_id` 与目标正式 UID。RPC 接受只表示请求已提交，必须等待递增
稳定快照确认；超时进入待确认，不报告成功。

运行时状态增量可投影固定原生快照中已知 UID 的最新状态，但不改变库存集合。视觉仓库保持只读；鼠标
扫描后的即时管理只能使用本次冻结的扫描索引计划。

仓库筛选使用正式 `suit_id`、`shape_id`、`property_id`：同组为“或”、跨组为“且”；状态固定为已装备、
已锁定、已弃置和其他。重置清除条件并恢复全部结果，不把当前页签当作隐含条件。评估角色范围只影响
状态规则评分，与计算角色、活动配装和倒带偏好隔离。

### 11.2 视觉扫描

- 扫描启动时创建不可变 dependencies，冻结账号、generation、目录、用户库、管理配置和热键绑定。
- OCR/视觉 Integration 只解析；文件生命周期由独立组件管理。
- 只有完整结果一次提交为快照；取消或异常不提交半成品。
- 缺失卡带数值按统一满级规则补全。
- 扫描后管理从末页按索引倒序定位；每次操作前后复核状态。锁定切换弃置时等待确认弹窗出现、确认并
  等待消失。任一定位、弹窗或复核失败即停止后续输入，并保留已完成项诊断。

### 11.3 鉴定与热键

鉴定把截图、剪贴板、手工输入和仓库单件入口统一投影为装备对象，只调用公共评分与展示，不写库存、
基础权重或配装。仓库单件鉴定调用公开鉴定 Service，不持有鉴定页面或 Controller。

扫描与鉴定共享应用级 `GlobalHotkeyManager`，owner 分别为 `scanning` 与 `identification`；同时只允许一个
会话，owner 只停止自己的会话，运行中的绑定冻结到任务结束。

## 12. 倒带推荐与游戏输入

倒带分析是独立只读入口：读取固定库存快照、当前账号 `rewind_recommendation` 偏好和各配装槽位活动
方案；偏好字段为 `target_character_ids`、`main_character_ids`、`strategy`、`target_grade`。打开页面或
修改选项不自动生成，只有显式“生成推荐”才求解。

```text
threshold = grade_ratio × max(1, drive_area) × 10
grade_ratio: D=0, C=.2, B=.3, A=.4, S=.5, SS=.6, SSS=.7, ACE=.8
gap = max(0, threshold - saved_assignment_score)
```

高于目标的分数不抵消其他驱动缺口。逐件评分优先读取 `payload.assignment_scores`，仅旧方案缺字段时
补算。每个方案按自己的 `source_snapshot_id` 解析。

- **全面均衡**：全部培养角色每种正缺分形状保留一个基础槽；余位按
  `形状评分缺口 / max(1, 形状库存)` 的整数比例分配。超过八种正缺分形状时返回提示。
- **少角冲分**：只看冲分角色；每种正缺分形状保留一个基础槽，余位只按形状评分缺口固定比例分配，
  不按低分驱动数量重复保留，也不除以库存。

奖池固定八槽，每槽概率 12.5%。同形状数量 `q` 的每槽价格为 `10 + 5 × (q - 1)`，总价为 `q × 每槽价格`；
8 种不同为 80、4 种各 2 次为 120、8 槽相同为 360。难度只决定蓝/紫/金品质，不进入形状、概率或价格
请求。

显式“进行倒带”后才进入输入 Integration，并冻结品质、定制模式和八槽方案。每个品质先切换对应难度，
等待刷新后读取该难度自己的右上角余额；余额不跨难度复用。初级随机十连固定消耗 600；紫/金定制从
右侧硬币口下方识别十连价格，不读取左侧单抽价格。“不做更改”进入现有定制但不改候选；“应用方案”
完成八槽配置。所有前置点击后等待 1 秒；十连循环固定为：

```text
点击投币10次 → 等待1秒 → Esc → 等待1秒 → Esc → 等待0.5秒 → 下一轮
```

执行期间 F12 可停止。该输入链在 `docs/roadmap.md` 标记为实验能力，真实环境验收前不得提升为稳定能力。

## 13. 装配、战报、设置与外部集成

### 13.1 极速装配与游戏界面自动装配

- 极速装配只消费 nte-core 正式 UID、角色实例和已保存槽位方案。
- 游戏界面自动装配消费视觉投影，可支持自建角色，但不伪装为正式 UID 链路。
- 批量任务冻结账号、generation、来源快照、目标槽位列表和角色项，并持久化事件与结果。
- 两条链路都在执行期间阻止账号切换，并以操作后的递增稳定快照确认最终状态。

极速装配每个角色最多发送三次完整装配请求。第一次或第二次不一致时卸空重装；每次重试前都等待递增
稳定快照。稳定快照等待失败立即停止，第三次仍不一致才生成最终报告。UI 分别展示：插件管道缺失、管道
存在但短暂不可用、队列繁忙、nte-core 请求超时、稳定快照等待超时、角色/实例/位置错误和装备集合不一致。

### 13.2 战报

战报使用 nte-core combat 会话的聚合事件与摘要，写当前账号历史。背包同步和战报不得争抢捕获会话。
聚合摘要不等于逐击数据；逐击、Buff/Debuff 区间、队伍、敌人实例与场景能力仅在上游提供正式 capability
后接入，不由 UI 或调试样本推测。

### 13.3 设置、更新与日志

- 设置页只接收 `AppContext`。主题写应用全局配置；其他偏好写当前账号或明确的应用级路径。
- 更新由 Mirror Controller/Integration 编排，不另建签名或下载事实源。
- 日志分层：基础设施管理 sink，Controller 记录操作生命周期，Service 记录业务阶段，DAO/Integration
  记录存储和外部交互，Domain 返回 diagnostics。
- 日志禁止完整 RPC、完整背包、UID 列表、账号显示名、用户绝对路径、OCR 全文、截图、CDK、Token、
  鉴权 URL 和可复原业务 payload。字段规范见 `docs/reference/logging-events.md`。

### 13.4 二进制与真实环境

nte-core、Npcap、dwmapi、mods 插件和游戏输入都属于 Integration。根目录本机二进制保持忽略；只有在
记录上游 commit/版本/许可、完成协议测试、打包测试和真实 Windows 验证后，才更新 `third_party` 发行
组件。Windows 验证器是维护工具，不进入安装包，也不替代人工游戏验收。

## 14. UI、代码与仓库门禁

一级导航固定为：工作台、计算、配装、角色、仓库、鉴定、战报、工具、设置。角色图纸和基础权重是角色
子页，通过 `parent_key` 保持父导航高亮。MainWindow 只负责组合、导航、账号切换、页面生命周期和退出。

- `src/`、`tools/`、`tests/` 中 Python 文件不得超过 800 行；按状态所有权拆分，不压缩代码规避。
- 新增 `type: ignore` 必须包含错误码与原因。
- Ruff `E9/F63/F7/F821/F401` 是全仓硬门禁。
- 新依赖同时更新 `pyproject.toml` 与 `uv.lock`；测试不依赖开发机偶然安装的包。
- feature 可复用公开组件与 contract；不得跨 feature 调下划线私有实现或访问其他页面 widget/worker。
- 新代码不得增加 `setattr(MainWindow, ...)`、`globals()` 动态导出、模块全局扫描、页面索引跳转或服务定位器。
- Release 由维护者本地构建并用 `gh` 手工发布；不建立自动发布工作流。
- Git 中不得出现账号库、WAL/SHM、日志、截图、PCAP、OCR 临时文件、安装器输出、本机绝对路径、
  本机 SDK/转储或未审计二进制。

## 15. 文档维护

`docs/` 只保留当前仍影响开发决策的稳定文档：

- `docs/README.md`：唯一索引；
- `docs/architecture.md`：系统边界与公共数据流；
- `docs/features.md`：现有功能原理与数据契约；
- `docs/integrations.md`：外部组件、协议和扩展边界；
- `docs/roadmap.md`：未完成事项与上游依赖；
- `docs/reference/`：公式、字段与专题 contract；
- `docs/validation/`：真实环境验收。

功能变化直接覆盖失效段落，删除过期规则、例外和计划，不追加“本次修改”“兼容到某版”“历史上曾经”
等流水账。一个事实只保留一个权威位置，其他文档通过链接引用。提交前检查全部 Markdown 相对链接。

## 16. 验证矩阵

| 改动范围 | 最小专项验证 |
| --- | --- |
| AppContext、账号、worker | app-context、account-user-database、settings-context |
| 同步、快照、运行时状态、视觉扫描 | inventory-sync、stabilizer、vision、streaming、mouse/gamepad、OCR golden |
| 计算、候选、默认配置、替换 | allocation、weighted-allocation、role-selector、crit、replacement |
| 自建角色、权重、图纸 | custom-role、character-weight、blueprint、graduation |
| 配装槽位、锁、游戏导入 | loadout-slot、loadout DAO、lock、game-loadout、equipment display |
| 仓库与鉴定 | warehouse、state-management、identification、hotkey boundary |
| 倒带 | rewind-shape-recommendation、shape-detection、toolbox、saved scores |
| 极速/自动装配 | equipment-apply、verification、bulk、drive-assembly |
| 战报 | battle-report DAO、persistence、capture 生命周期 |
| SQLite/发行数据 | migration、static-data、shared-data、manifest、catalog |
| 文档、依赖、打包 | Markdown 链接、dependency/module-boundaries、repository-hygiene、packaging |

权威命令：

```powershell
python tools/quality/run_tests.py core
python tools/quality/run_tests.py full
$mypyFiles = Get-Content tools/quality/mypy_allowlist.txt
python -m mypy $mypyFiles
python -m ruff check .
python -X pycache_prefix=build/compile-cache -m compileall -q src tests tools
uv lock --check
git diff --check
```

涉及真实游戏、驱动、插件或更新时，再执行 `tools/windows_validation` 与 `docs/validation/windows.md`。
完成前确认：静态库及 manifest 只包含预期变更；迁移可重试；文档链接有效；本机数据未进入 Git；上游
输入、失败路径、下游保存、最终确认和回滚行为均有测试或人工验收记录。
