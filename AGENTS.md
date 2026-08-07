# NTE Drive Calc 开发接手指南

本文用于帮助后续开发者快速理解项目，而不是记录历次优化过程。开发前先确认三件事：

1. 业务数据属于发行静态、本机共享还是当前账号；
2. 功能消费的是哪个固定快照、配置版本和账号 generation；
3. 改动是否破坏了下文定义的功能契约或依赖方向。

技术文档入口是 `docs/INDEX.md`。阅读顺序为“本文 → 文档索引 → 对应模块当前能力 → 对应版本
开发计划”。阶段计划、迁移进度和临时验收记录写入 `docs/development/<version>/`，不要追加到
本文；模块现有能力和数据边界写入 `docs/modules/`，不能把未实现计划描述成当前能力。

## 1. 产品与运行边界

NTE Drive Calc 是开源 Windows 游戏管理工具，核心业务是：

- 从游戏同步或扫描驱动、卡带和角色实例；
- 按角色配置、套装、形状、词条和优先级计算配装；
- 保存、比较、鉴定和管理装备；
- 通过 nte-core 或游戏界面自动化执行装配；
- 维护账号角色养成、基础权重和角色图纸。

长期产品决策：

- 程序要求管理员权限，这是抓包、驱动和游戏交互的运行前提。
- `nte-core` 是背包同步、仓库状态写回和极速装配的正式链路。
- 视觉扫描是兼容/兜底链路，最终也写入统一的 SQLite 快照模型。
- 不提供完整撤销；提供任务记录、结果确认、失败重试和插件安全还原。
- 不划分普通/专家模式；进阶选项放在其实际影响的角色或功能附近。
- 更新来源、下载和文件风险交给 Mirror；应用不另建更新签名链。
- Release 由维护者本地构建并使用 `gh` 手工发布，不建立自动发布工作流。
- Windows 验证器是维护工具，不进入用户安装包，也不代替真实游戏人工验证。

## 2. 系统地图

### 2.1 依赖方向

```text
UI Page / View
        ↓
Controller + immutable dependencies
        ↓
Application Service
      ↙       ↘
Domain /      DAO / Integration
Optimizer       ↓
          SQLite / nte-core / 文件系统
```

- UI 收集输入、展示结果，不承载 SQL、协议、OCR 或优化算法。
- Controller 拥有一次操作的 worker、取消和 UI 状态投影。
- Service 编排业务步骤，返回 Qt 无关的结果。
- Domain/Optimizer 是纯业务计算，不读取数据库、Qt、日志或当前账号全局状态。
- DAO 独占 SQL、事务、schema 和迁移。
- Integration 独占外部协议、进程、驱动、OCR 和文件格式。

禁止反向依赖和隐式耦合：

- `domain`/`optimizer` 不得导入 `ui`、`features`、DAO 或 Integration；
- `services` 不得导入 Qt 页面、弹窗、MainWindow 或具体 feature；
- DAO/Integration 不得导入 UI；
- feature 之间不得调用下划线开头的私有实现；
- 页面不得访问另一个页面的 widget、worker 或内部字段；
- 跨功能 UI 或操作系统能力由组合根创建并注入，Controller 不得创建、转发或充当其他
  Controller 的服务定位器；
- 不得通过 `setattr(MainWindow, ...)`、模块全局扫描或页面索引安装功能；
- 中文显示名只用于展示，跨层关系使用官方 ID。

### 2.2 主业务链

```text
nte-core 事件 ─┐
               ├→ 稳定化 → 不可变背包快照 → 计算上下文 → 配装预览
视觉扫描结果 ──┘                                  ↓
                                            保存配装方案
                                                  ↓
                                 nte-core 极速装配 / 游戏界面自动装配
                                                  ↓
                                        新稳定快照最终确认
```

所有计算、保存、替换和装配必须能追溯到同一个 `snapshot_id`。所谓“当前背包”只允许在
操作开始前解析一次；操作进行中不得重新跟随最新指针。计算开始时还必须冻结活动配装的
`AllocationLockSnapshot`；它是同一账号内的方案保留契约，不是游戏客户端的装备锁。

## 3. 数据所有权

### 3.1 三个 SQLite 数据域

| 数据域 | 文件 | 拥有内容 | 写入者 |
| --- | --- | --- | --- |
| 发行静态 | `data/game_static.sqlite3` | 官方角色、装备、套装、形状、属性、成长、技能、敌人、推荐权重、默认图纸和数据集来源 | 仅 `tools/game_data` |
| 本机共享 | `app_shared.sqlite3` | 明确需要跨全部账号共享的用户覆盖，目前主要是额外形状差异 | `SharedDataDao` |
| 账号数据 | `accounts/<account_id>/user_data.sqlite3` | 背包快照、角色实例、角色配置、基础权重、计算偏好、配装方案及其计算保留锁、装配任务和账号设置 | `UserDataDao` 及应用服务 |

静态库运行时只读。每次重新生成都必须同步更新 `data/manifest.json`；应用或测试运行前后
不应改变静态库 SHA-256。

从 `origin`、`upstream` 或其他合作分支拉取、合并或复制 `data/game_static.sqlite3`，也视为一次
静态库更新，不能因为文件来自上游提交就跳过 manifest 校验。同步后必须核对数据库完整性、
内嵌 dataset/schema/importer 元数据和实际 SHA-256；数据库确认来自目标上游最新提交且检查正常
时，以该数据库为权威，在同一变更中同步 `data/manifest.json`，不得为迁就过期 manifest 回退
较新的数据库。至少运行 `tests.test_static_data_manifest`；合并或发布前再运行 `full`。

共享库只保存相对发行默认的差异。删除覆盖表示恢复默认，不复制整张官方表。

账号库按迁移版本演进：

- 已发布迁移的含义不可修改；
- schema 变化必须同时验证新库创建、旧库升级、失败回滚和重试；
- 页面和 Service 不拼 SQL，只通过 DAO 读写；
- 账号切换后不得继续使用旧 DAO、旧路径、旧缓存或旧 worker。

### 3.2 读取优先级

```text
账号明确配置
  ↓ 未设置且该配置允许共享
本机共享覆盖
  ↓ 无覆盖
发行静态默认
```

不是所有配置都存在三层。例如额外形状使用“共享覆盖 → 发行默认”；账号基础权重使用
“账号配置 → 发行推荐”，两者不能混写。

### 3.3 文件数据

- 账号配置、截图、日志和临时 OCR 文件必须位于当前 `AccountContext` 指定目录。
- 发行资源通过 `src.integrations.bundled_resources` 定位，不能用它推导可写账号路径。
- 不从当前工作目录、环境变量或 MainWindow 动态属性猜测账号数据位置。
- 不提交账号库、WAL/SHM、日志、截图、安装器、验证报告或本机绝对路径。

## 4. 账号、状态与后台生命周期

`src.app.context` 是运行时路径和账号状态的唯一组合根：

- `ApplicationPaths`：不随账号切换的应用、资源、静态库和共享库路径；
- `AccountContext`：账号 ID、用户库、配置、截图和日志目录；
- `AppContext.generation`：账号上下文代次。

长任务启动时冻结：

- 账号 ID 和账号数据库绝对路径；
- `AppContext.generation`；
- `snapshot_id`；
- 计算配置或 profile version；
- 该功能需要的资源和输出目录。

回调落地前重新核对 token、generation 和账号路径。旧结果静默丢弃，绝不能写入新账号。

账号切换顺序固定为：

```text
检查/停止账号后台任务
  → 切换 AccountContext 并递增 generation
  → 重建账号服务与窄 dependencies
  → 通知页面清空账号缓存
  → 仅恢复切换前允许自动运行的服务
```

worker 引用在初始化前和释放后都可能为 `None`。生命周期检查必须先复制局部引用，再判断
`worker is not None and worker.isRunning()`；`hasattr` 不能证明对象有效。

MainWindow 只拥有顶层导航、账号切换、页面创建销毁和应用退出。具体业务对象分别由页面、
Controller 或 Service 持有。

## 5. 功能域与业务契约

### 5.1 工作台、账号与背包同步

业务：展示当前账号、环境健康、同步状态、稳定背包摘要和主要入口。

链路：

```text
nte-core snapshot event
  → InventorySnapshotStabilizer
  → InventorySyncService
  → UserDataDao.import_inventory_snapshot
  → 当前稳定快照通知
```

关键约束：

- 同一账号同一时刻只有一个同步实例。
- 只有完整且稳定的事件才能成为当前快照；失败不能生成伪快照。
- 新快照一次事务提交装备、角色实例和当前指针。
- `inventory.get_latest` 只读取 nte-core 最近抓到的数据，不能描述为强制游戏刷新。
- 独立 `characters` 列表是极速装配角色 UID 完整性的依据；从已装备物品推导的旧映射只可
  用于兼容展示。
- 同步日志只记录数量、generation、sequence、阶段和安全错误码，不记录 UID 列表或 RPC。

主要实现：`src/services/inventory_sync_*`、`src/storage/sqlite/inventory_snapshot_dao.py`。

### 5.2 计算与优化器

业务：在固定背包内，为一组按优先级排列的角色分配唯一驱动和卡带。

输入包括角色顺序/平级组、套装要求、卡带主词条、副词条优先级与黑名单、评分等级、
暴击限制、账号基础权重、额外形状和固定快照。输出是不可变
`WeightedAllocationPreview`，包含账号、静态数据集、快照和 profile version。

核心语义：

- 已分给前序角色的真实装备不再进入后续角色候选池；平级角色由同一求解过程处理。
- 默认套装要求是四件套，只有玩家显式修改时才使用自定义要求。
- 驱动副词条黑名单是最先执行的硬过滤；它不淘汰卡带。
- 副词条顺序模式使用嵌套前缀池：先取最深非空层，再在该层比较基础评分。
- 副词条一致模式按命中数量形成层级池。
- 卡带先执行套装和主词条硬过滤，再执行副词条池；主词条优先于副词条。
- 开启“不限制评分等级”只取消评分门槛，不取消玩家明确设置的词条硬过滤。
- 驱动可在副词条池为空时回退普通候选以保证图纸可填；卡带明确自选时不得绕过硬过滤。
- 配装锁定在构造装备候选对象之前执行：所有锁定方案的真实 UID 先从库存剔除，之后才可
  执行评分、黑名单、主词条、副词条与图纸筛选。它同时适用于角色优先和平级组、全局最优，
  也同时适用于 nte-core 与视觉扫描提交的稳定快照。
- 锁定角色不参与本次求解，也不会由本次保存覆盖；锁定方案保持其原始来源快照和方案内容。
  其余角色不能借用锁定的卡带或驱动，哪怕该锁定角色没有被勾选参与本次计算。
- `AllocationLockSnapshot` 必须校验每个锁定 UID 在计算固定快照中仍存在且 kind 一致；失效
  锁定阻止计算，不得静默放行或假装该装备未锁。保存前必须重建并比较该快照，锁定在计算
  期间变更时要求重新计算。
- Optimizer 只消费冻结上下文，不读取页面、当前账号或 SQLite。

基础权重来源于账号配置或发行推荐。计算页角色管理不编辑权重，只把当次有效值写入版本化
profile，以保证结果可复现。

保存和替换必须基于原 preview；不能重新打开“当前账号/当前快照”补齐旧结果。

主要实现：`src/services/allocation_*`、`src/optimizer`、
`src/features/weighted_allocation`。

### 5.3 角色、基础权重与角色图纸

业务：管理角色养成状态、弧盘、当前面板、边际收益、账号基础权重和角色图纸。

角色数据分工：

- 官方角色、成长、技能、弧盘和推荐值来自静态库；
- 等级、突破、觉醒、技能选择和弧盘指针写当前账号库；
- 基础权重写当前账号库；
- 额外形状用户覆盖写共享库，并在 UI 明确“全部账号共享”；
- 图纸求解冻结账号、generation、静态库、用户库和输入配置。

“基础权重”与角色页“词条权重（只读）”不是同一数据：

- 基础权重包含驱动副词条权重和卡带主词条权重，是持久化计算输入；
- 未编辑账号使用可随发行数据刷新的推荐值，真正编辑后变为账号自定义；
- 角色页只读最终权重按当前面板的直伤边际收益动态归一化；
- 未进入直伤公式的属性保留基础权重；
- 动态最终权重不写数据库，只用于角色分析和替换候选排序；
- 角色页“保存”只保存养成指针，不会覆盖基础权重。

角色图纸和基础权重是角色域子页面，不拥有独立业务状态；返回角色页时必须重新读取有效
账号数据，不能缓存跨账号 DAO。

主要实现：`src/features/official_role`、`src/features/configuration`、
`src/features/blueprints`、`src/services/official_role_*`、
`src/services/character_weight_service.py`。

### 5.4 仓库与鉴定

仓库与鉴定是两个独立功能域。仓库从固定稳定快照读取批量库存；鉴定从截图、剪贴板、
手工输入或仓库单件入口产生一个待评分装备。二者可以共享公开解析、评分和展示契约，
但不能互相持有页面或 Controller。

仓库链路：

- `WarehouseInventoryService` 读取快照并生成原始投影；
- Presenter/View Model 负责显示名称、图标和标签；
- `WarehouseStateManagementService` 生成并复核状态计划；
- `WarehouseStateWriter`/nte-core Integration 独占游戏状态写回。

仓库写回必须遵守：

- 计划固定 `snapshot_id` 和目标 UID；
- RPC 接受只表示指令已提交，不表示游戏快照已确认；
- 后续递增稳定快照核对目标状态，收到中间快照继续等待；
- 超时显示“待快照确认”，不得伪造成功；
- 视觉扫描库存没有可靠锁定/弃置状态，不可伪装成可写 nte-core 库存。

鉴定链路：

- 每次图片解析冻结账号 ID、generation、截图/配置目录和用户库；
- OCR/手工输入先收敛为统一装备对象，再调用公开评分与角色适配 Service；
- `IdentificationController` 只拥有鉴定 worker、连续截图会话、页面临时输入和结果状态；
- 仓库卡片鉴定与一级“鉴定”页面共享公开 Service，不调用对方页面方法；
- 装备卡片由组合根注入的 `EquipmentPresentation` 生成，鉴定 Controller 不复制评分或
  卡片实现，也不从扫描 Controller 获取工厂函数；
- 连续截图通过组合根注入的 `GlobalHotkeyManager` 建立 owner 为 `identification` 的会话；
  F9/F10 等具体绑定从该会话的冻结配置读取，完成时按 owner 释放；
- 连续截图热键活跃期间，鉴定视为正在运行并阻止账号切换；F12 不冒充完成操作，用户仍以
  “完成截图”热键结束并回到鉴定页。

主要实现：`src/features/inventory`、`src/features/identification`、
`src/services/warehouse_*`、`src/services/warehouse_identification_service.py`、
`src/ui/equipment_presentation.py`、`src/integrations/global_hotkeys.py`。

### 5.5 配装保存与自动装配

配装页面读取当前账号已经持久化的方案，展示来源快照、装备、评分、收益、替换差异、计算
保留锁和执行入口。保存方案属于账号数据；展示对象只持有可丢弃的 UI 状态，不是方案权威
来源。

计算保留锁规则：

- 锁定状态属于活动 `loadout_plan`，只在当前账号生效；它绝不调用、映射或伪装为游戏的
  锁定 RPC。每张方案卡的“装配”按钮右侧显示“锁定/解除锁定”。
- 可以锁定不完整方案，但必须至少有一张真实卡带；任何虚拟占位、空方案或无卡带方案均不
  能锁定。锁定时只校验方案结构，计算时再校验它在当前稳定快照中的实际 UID。
- 锁定方案不可删除、重新保存或单件替换；必须先解除锁定。批量清空跳过锁定方案并明确
  报告。DAO 还必须拒绝其他方案借用锁定 UID、将锁定方案虚拟化，或在冲突修复中静默破坏
  锁定方案。
- 替换优化的候选列表必须直接排除其他锁定方案的真实装备；保存事务仍以 DAO 的锁定检查
  作为最终防线。UI 过滤不是安全边界。

两条执行链：

- 极速装配：使用 nte-core 稳定快照、角色实例 UID 和保存方案；
- 游戏界面自动装配：使用应用自己的视觉识别、虚拟手柄和输入执行。

共同约束：

- 方案必须保留来源 `snapshot_id`，装备使用正式游戏 UID；
- 视觉临时 UID 不得进入极速装配；
- `BulkEquipmentApplyService` 是批量 nte-core 装配唯一编排者；
- 任务、角色项和事件写账号库，失败项可重试；
- 装配期间阻止账号切换；
- F12 只在安全检查点停止；
- 游戏状态最终由新的稳定快照确认，不以 UI 输入完成作为权威结果。

`EquipmentPresentation` 是独立的公共 UI 组件：

- `src.ui.app` 在组合根只创建一个实例，并把它显式注入计算 Controller 和鉴定 Controller；
- 组件统一维护装备卡片、评级、属性收益、方案差异和计算结果区域的展示规则；
- `AllocationController` 只提交不可变结果与方案上下文，不把展示能力转交给
  `ScanningController`；
- 已保存配装通过公开 `equipment_display_context` 取得同一组件，不复制私有渲染函数；
- 鉴定只调用公开 `equipment_card` 边界，不读取计算结果 widget 或方案状态；
- 目录加载阶段由组合根更新角色、评分器和形状目录，任一 feature 不得自行创建第二份组件；
- 该组件不写 SQLite、不选择“当前快照”、不启动 worker，也不执行装配。

扫描 Controller 不提供装备卡片、展示对象或配装接口。禁止恢复
`scanning_controller.equipment_card`、`scanning_controller.equipment_presentation` 或以
回调包装这些接口。

主要实现：`src/features/drive_assembly`、`src/services/bulk_equipment_apply_service.py`、
`src/services/equipment_apply_service.py`、`src/ui/equipment_presentation.py`、
`src/features/inventory/equipment_display_context.py`、相关 Integration。

### 5.6 视觉扫描与 OCR

视觉扫描是在 nte-core 不可用或需要图片输入时，从游戏画面批量解析装备并提交视觉快照。
它与鉴定共享底层公开解析契约及应用级热键基础设施，但不拥有鉴定功能。

- 每次任务创建不可变 dependencies，冻结账号、generation、截图/模板目录和用户库。
- OCR/视觉 Integration 只解析并返回装备，不直接写账号库。
- `ScanFileLifecycle` 管理成功、失败、去重、重命名和清理。
- `import_vision_inventory` 是视觉库存进入 SQLite 的唯一提交边界。
- 取消或异常不能提交半成品快照。
- 扫描和鉴定通过公开解析 contract 共享能力，不互相调用 worker 或私有函数。
- 日志记录分辨率、DPI、阶段、数量和耗时，不记录 OCR 全文或截图内容。

热键边界：

- `GlobalHotkeyManager` 独占应用级监听线程、Windows `RegisterHotKey`/keyboard fallback、
  热键配置和当前 owner；它不得导入扫描、鉴定或 Qt 页面；
- 同一时刻只有一个热键会话。新会话会使旧 generation 失效，owner 只能停止自己的会话；
- 运行中的会话冻结启动时配置，设置页的新绑定只对下一次会话生效；
- `ScanningController` 只在扫描开始/结束时登记或释放 owner 为 `scanning` 的动作；
- 扫描动作投影位于 `src/features/scanning/hotkey_actions.py`，只操作当前扫描 worker；
- `IdentificationController` 直接登记自己的截图/完成回调，扫描 Controller 不保存鉴定引用，
  也不提供通用注册/反注册方法；
- 应用退出时由组合根关闭管理器；账号切换前活动功能必须按各自生命周期结束。

视觉快照和 nte-core 快照使用同一库存表，但来源能力不同；下游必须检查 `source`，不能仅凭
“存在快照”推断可写状态或角色实例完整。

主要实现：`src/features/scanning`、`src/scanner`、
`src/integrations/vision`、`src/integrations/global_hotkeys.py`、
`src/services/streaming_scan_service.py`。

### 5.7 设置、环境、更新与日志

设置页只接收当前 `AppContext`：

- 账号设置写当前账号库；
- Npcap、nte-core、dwmapi、插件和资源诊断使用应用级路径；
- Mirror 检查、下载和安装由更新 Controller/Integration 负责；
- 页面不拼更新 URL、不记录 CDK、Token 或鉴权信息。

日志分层：

- 基础设施管理 sink、格式、轮转、保留和 UTF-8；
- Controller 记录操作/worker 生命周期；
- Service 记录业务阶段；
- DAO/Integration 记录存储或外部交互；
- Domain/Optimizer 返回 diagnostics，不导入日志。

常驻日志为 INFO；用户开启“运行日志”后，每次会话创建独立
`nte_runtime_YYYYMMDD_HHMMSS[_N].log`，记录 DEBUG 以上。账号切换先 flush/关闭旧 sink，
再在新账号目录创建 sink。

核心事件必须具有 `operation_id`、功能名、阶段、耗时和安全上下文。字段规范维护在
`docs/reference/logging-events.md`。禁止记录完整 RPC、完整背包、账号显示名、用户绝对路径、
OCR 全文、截图和任何鉴权材料。

## 6. 跨功能共享契约

| 契约 | 生产者 | 消费者 | 不得做的事 |
| --- | --- | --- | --- |
| `AppContext` / generation | 应用组合根 | Controller、窄 dependencies | 下层持有 MainWindow 或猜路径 |
| 不可变背包快照 | 同步/扫描提交 Service | 仓库、计算、装配 | 计算中跟随最新快照 |
| 角色实例映射 | nte-core 快照 DAO | 极速装配、仓库展示 | 把视觉 UID 当正式 UID |
| 账号基础权重 | 基础权重 Service | 计算上下文、角色分析、鉴定 | 角色页动态权重反写基础权重 |
| 版本化优化 profile | 计算偏好 Service | Allocation Context | worker 读取页面控件 |
| `WeightedAllocationPreview` | 计算 worker | 结果、保存、替换 | 旧账号回调写新账号 |
| `AllocationLockSnapshot` | 配装锁定 Service/DAO | 计算 worker、保存边界 | 把游戏装备锁当作方案锁、在候选筛选后才排除 UID |
| 保存配装方案 | 结果保存 Service | 配装页、装配 Service | 从显示名称反查官方关系 |
| 装配任务/事件 | Apply Service/DAO | 进度页、验证器 | UI 直接写任务表 |
| `EquipmentPresentation` | 应用组合根 | 计算结果、已保存配装、鉴定 | Controller 创建副本、转发工厂或写业务数据 |
| `GlobalHotkeyManager` | 应用组合根 | 扫描、连续截图鉴定 | feature 持有监听线程、跨 owner 停止或反向引用 Controller |
| 结构化日志事件 | Controller/Service/Integration | 文件 sink、验证器 | 将业务 payload 当日志字段 |

新增跨功能共享时，优先建立公开不可变 contract 或 Application Service；不要通过 MainWindow
字段、页面私有函数或兼容模块形成暗链。

公共组件的唯一组装位置是 `src.ui.app`。Controller 是这些组件的消费者，不得成为公共能力
的生产者、代理层或服务定位器。需要新增消费者时修改组合根和公开构造参数，并同步增加
依赖边界测试；禁止恢复扫描 → 鉴定、配装 → 扫描或页面 → 页面暗链。

## 7. UI 信息结构与状态所有权

一级页面：

- 工作台：账号、同步、环境摘要和快捷入口；
- 计算：角色选择、优先关系、约束与求解；
- 配装：保存方案、替换优化和自动装配；
- 角色：养成、弧盘、面板和角色域子功能；
- 仓库：快照浏览、比较和状态管理；
- 鉴定：截图、剪贴板和手工单件鉴定；
- 设置：环境、驱动、更新、日志、数据库和账号选项。

角色图纸、基础权重属于角色子页面；进阶页保持所属一级导航高亮。

页面拥有控件输入、选择和可丢弃展示缓存；Controller 拥有 worker 和忙碌状态；Service/
DAO 拥有业务与持久化。页面销毁、账号切换和应用退出都必须走各功能公开停止入口。

## 8. 已完成的工程基线

后续开发应在这些现有能力上扩展，而不是恢复旧入口：

- 运行时路径和账号状态已统一到 `AppContext`；
- 官方静态、共享覆盖和账号数据已拆为三个 SQLite 数据域；
- nte-core 和视觉库存已收敛为不可变快照模型；
- 计算请求已冻结账号、快照、静态数据集和 profile version；
- 角色权重、额外形状、图纸和动态最终权重的数据归属已经分离；
- 仓库状态写回、极速装配和结果确认已由 Service/Integration 编排；
  - 装备展示和全局热键已从扫描 Controller 拆为组合根持有的独立公共组件；
  - 配装锁定已收敛为账号 SQLite 的不可变计算保留快照，覆盖两种库存来源、两种分配策略和
    保存/替换事务边界；
- 扫描、鉴定、仓库、计算和配装不再通过 Controller 转发共享 UI/系统能力；
- 核心功能具有结构化日志和独立时间戳运行日志；
- 数据迁移、依赖边界、打包资源、类型检查和仓库卫生已有自动测试；
- Windows 半自动验证器只读收集环境、进程、数据库和日志证据。

不要恢复 `src/app/runtime.py`、旧 JSON 角色权重入口、页面间私有 mixin 调用或 release 工作流。

## 9. 工程、发布与验证

### 9.1 工程元数据

- 版本唯一来源：`src/app/version.py::__version__`；
- 依赖唯一声明：`pyproject.toml`；
- 可复现锁：`uv.lock`；
- 根目录不保留 `requirements.txt`；
- 无 uv 环境使用 `python -m pip install .`；
- 单个 Python 文件默认不超过 800 行，按功能/状态所有权拆分，不压缩代码规避；
- 新增 `type: ignore` 必须写错误码和具体原因；
- Ruff 的 `E9/F63/F7/F821/F401` 是全仓硬门禁。

### 9.2 发布与打包

- `tools/release/prepare_release.py` 只检查、构建并输出维护者命令；
- 安装包同时生成 `.sha256`；
- GitHub Release 由维护者使用 `gh` 手工发布；
- Mirror 上传和 GitHub 发布不能成为构建脚本的隐式副作用；
- 测试、验证器、账号库、日志、截图和本机路径不得进入安装包；
- 静态库、manifest、nte-core、插件和 schema 必须有打包断言与来源说明。
- 根目录本机 `nte-core.exe`、`dwmapi.dll` 和本机插件副本必须保持 Git ignore；只有明确晋升为
  发行组件、记录上游 commit/版本/许可并完成协议与打包验证后，才能更新 `third_party` 中被追踪
  的正式二进制。不得把本机编译结果直接覆盖并提交为发行组件。

### 9.3 测试入口

| 改动 | 最小验证 |
| --- | --- |
| AppContext、账号、后台任务 | `test_app_context`、`test_account_user_database` |
| 同步、快照、角色实例 | `test_inventory_sync_service`、`test_inventory_snapshot_stabilizer` |
| 计算、约束、替换 | allocation、weighted-allocation 相关测试 |
| 角色、权重、图纸 | official-role、character-weight、blueprint 相关测试 |
| 仓库、鉴定 | warehouse、identification 相关测试 |
| 扫描、OCR | scanning、streaming、OCR golden sample 相关测试 |
| 自动装配 | equipment-apply、drive-assembly 相关测试 |
| 设置、更新、日志 | settings、update、runtime/observability logging |
| SQLite、静态数据 | migration、static-data、manifest 相关测试 |
| 依赖、类型、仓库卫生 | dependency-boundaries、mypy、repository-hygiene |
| 打包、真实 Windows | packaging 测试、Windows 验证器和人工清单 |

权威入口：

```powershell
python tools/quality/run_tests.py core
python tools/quality/run_tests.py full
python -m mypy <tools/quality/mypy_allowlist.txt 中列出的文件>
python -m ruff check .
python -X pycache_prefix=build/compile-cache -m compileall -q src tests tools
python -m uv lock --check
git diff --check
```

`core` 按功能类型自动选择并并行分片；共享状态问题可用 `--jobs 1`。`full` 是合并与发布前的
权威单进程回归。涉及真实游戏输入、驱动、插件或更新时，再执行
`tools/windows_validation` 和 `docs/validation/windows-manual-validation.md`。

## 10. 开发变更清单

开始修改前回答：

1. 输入数据属于哪个账号、快照和配置版本？
2. 输出写入静态库、共享库、账号库还是只返回内存结果？
3. 页面、Controller、Service、DAO/Integration 分别拥有什么状态？
4. worker 如何取消，账号切换和退出时如何停止？
5. 旧 generation、旧 token 和旧快照结果如何丢弃？
6. 哪些字段进入日志，是否满足脱敏要求？
7. 哪些公共行为测试证明功能契约没有被破坏？

实施顺序：

```text
确认公共行为
  → 建立/修改公开 contract
  → Service/DAO/Integration 实现
  → Controller/UI 接入
  → 迁移旧调用
  → 删除兼容入口
  → 专项、core、full 和静态检查
```

纯重构不要同时改变评分、配装、OCR、战斗或数据库语义。必须改变业务规则时，先更新对应
公共行为测试和本文中的长期契约；具体实施过程仍写任务文档。
