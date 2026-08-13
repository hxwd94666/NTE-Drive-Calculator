# NTE Drive Calc 开发契约

本文件是仓库级开发契约，面向维护者与编码代理。它同时说明“产品必须保持的行为”和“代码应继续迁移
的方向”，不假定现有仓库已经完全符合目标架构。技术说明从 `docs/README.md` 进入。

本文件应作为普通文本随仓库提交，并放在 Git 根目录以约束整个项目。内容只写可公开的工程规范，
不得包含账号、Token、CDK、本机绝对路径、私有服务地址、真实 UID 或未脱敏运行数据；局部目录若有
不同规则，再在该目录放置更窄的 `AGENTS.md` 或 `AGENTS.override.md`。

## 1. 契约等级与开发方式

本文件中的规则分为三类：

1. **产品硬契约**：数据所有权、账号隔离、不可变快照、正式 ID/UID、评分与候选规则、保存与写回语义。
   修改前必须先更新公共行为测试和本文，不能用重构、兼容或性能优化名义改变。
2. **工程硬门禁**：静态库只读、迁移只追加、日志脱敏、依赖与版本唯一来源、基础静态检查，以及新代码
   不得引入新的反向依赖、动态注入或跨功能私有调用。
3. **目标架构**：UI、Controller、Service、Domain/Optimizer、DAO/Integration 的职责方向。新功能和
   实质性重写必须遵守；已知存量例外可以原地维护，但不得复制、扩散或被描述成推荐写法。

开始修改前先回答：

开始修改前先回答：

1. 输入属于发行静态、本机共享还是当前账号？
2. 功能冻结哪个账号 generation、`snapshot_id`、配置/profile 版本和静态数据集？
3. 输出写入哪个数据域，还是只返回内存结果？
4. 页面、Controller、Service、DAO/Integration 分别拥有什么状态？
5. worker 如何取消，旧 generation、旧 token 和旧快照结果如何丢弃？
6. 哪些公共行为测试证明上下游契约未被破坏？

业务能力新增或语义变化按以下顺序实施：

```text
确认公共行为 → 修改公开 contract → Service/DAO/Integration → Controller/UI
→ 迁移调用方 → 删除旧入口 → 专项测试 → core/full 与静态检查
```

存量例外中的局部修复先固定现有公共行为，再做最小范围维护；不要求在每次业务修复中顺带完成整条架构
迁移。架构迁移应作为独立任务，先列调用方和退出条件，再逐层迁移并删除旧入口。纯重构不得同时改变
评分、OCR、伤害、配装或数据库语义。未完成事项写入 `docs/roadmap.md`，不要描述成当前能力。

## 2. 目标分层与当前边界

目标依赖方向是：

```text
UI Page / View
      ↓
Controller + immutable dependencies
      ↓
Application Service
   ↙                 ↘
Domain / Optimizer       DAO / Integration
                              ↓
                       SQLite / nte-core / 文件系统 / OCR
```

- UI 只采集输入、展示结果和保存可丢弃交互状态；新页面不直接打开 DAO，不承载 SQL、协议、OCR 或算法。
- Controller 拥有一次操作的 worker、取消和 UI 状态投影，通过窄依赖调用 Service/Integration，不代理
  其他 Controller。
- Service 冻结输入、编排业务步骤和事务，返回 Qt 无关结果。
- Domain/Optimizer 接收完整输入并纯计算，不主动查找当前账号、数据库、Qt、日志或外部进程。
- DAO 独占 SQL、schema、迁移和事务；Integration 独占协议、进程、驱动、OCR 和外部文件格式。
- feature 可复用公开组件和公开 contract；不得跨 feature 调用下划线私有实现，也不得访问另一页面的
  widget、worker 或内部字段。
- 公共组件在 `src.ui.app` 组合根创建并显式注入。新代码禁止 `setattr(MainWindow, ...)`、模块全局扫描、
  页面索引跳转和 Controller 服务定位器。
- 中文显示名只用于展示；跨层关系使用官方 `character_id`、`item_id`、`suit_id`、`shape_id`、
  `property_id` 和正式 UID。

### 2.1 已知存量例外

以下是当前仓库事实，不是新代码范例：

- `src.optimizer.scoring.ScoringEngine` 在未注入角色评分目录时仍保留 SQLite 兼容读取；目标是由 Service
  构造不可变评分输入后注入，旧入口迁完再删除回退。
- 鉴定、配装展示、加权配置和扫描管理的部分 Page/View/Controller 仍直接读取 DAO；维护时可修复原路径，
  新功能不得继续增加 UI 直连，迁移时按“静态目录、固定快照投影、账号偏好”拆成窄 Service。
- `src.features.inventory.page` 仍是动态导出旧 MainWindow 方法的兼容门面；允许保留兼容，不得新增导出，
  只有调用方完成迁移后才能删除。
- 部分旧 worker 检查尚未满足下文统一生命周期要求；修改相关代码时不得扩大例外。

### 2.2 存量例外处理原则

- **允许维护，禁止复制**：可以修复异常、补测试和保持兼容，不得在新模块照搬旧依赖方式。
- **业务与迁移分开**：修复评分、OCR、配装等业务问题时不强制大范围重构；架构迁移不顺带改变业务语义。
- **迁移完整切片**：需要迁移时，从公开 contract、Service 到调用方一次完成最小闭环，不新增永久双入口。
- **例外只减不增**：退出条件满足后直接删除本节对应项，不追加版本流水账或“曾经如何”的历史说明。

## 3. 应用上下文与账号生命周期

`src.app.context` 是运行时路径和账号状态的唯一组合根：

- `ApplicationPaths`：应用、资源、发行静态库和共享库路径；
- `AccountContext`：账号 ID、用户库、配置、截图和日志目录；
- `AppContext.generation`：账号上下文代次。

长任务启动时冻结账号 ID、用户库绝对路径、generation、`snapshot_id`、配置/profile 版本和输出目录。
回调落地前重新核对 token、generation 和账号路径；旧结果静默丢弃，不能写入或展示到新账号。

账号切换顺序：

```text
检查并停止账号后台任务 → 切换 AccountContext、generation +1 → 重建窄服务
→ 页面清除账号缓存 → 仅恢复允许自动运行的服务
```

worker 在初始化前和释放后都可能为 `None`。生命周期判断先复制局部引用，再检查
`worker is not None and worker.isRunning()`。应用退出也必须走各功能公开停止入口。

## 4. 数据所有权与 SQLite

| 数据域 | 文件 | 内容 | 写入者 |
| --- | --- | --- | --- |
| 发行静态 | `data/game_static.sqlite3` | 官方角色、装备、套装、形状、属性、成长、技能、敌人、推荐权重、默认图纸、数据来源 | 仅 `tools/game_data` |
| 本机共享 | `app_shared.sqlite3` | 明确跨全部账号共享的差异，目前主要是额外形状 | `SharedDataDao` |
| 应用全局 | `config/global_ui_preferences.json` | 当前主题；切换账号时不得重新加载或覆盖 | 全局主题设置 Service |
| 账号私有 | `accounts/<account_id>/user_data.sqlite3` | 背包快照、角色实例、养成、基础权重、计算偏好、配装、锁、任务、战报和账号设置 | `UserDataDao` 与应用服务 |

读取优先级是“账号明确配置 → 允许共享时的共享覆盖 → 发行默认”。额外形状使用“共享覆盖 → 发行
默认”；角色基础权重、计算偏好、倒带偏好、配装和任务只属于当前账号，不能混写。

静态库运行时只读。重新生成、从上游同步或替换静态库时，以确认后的数据库为权威，并在同一变更中
更新 `data/manifest.json` 的 dataset/schema/importer 元数据和 SHA-256。至少运行
`tests.test_static_data_manifest`，合并或发布前运行 full。

账号库迁移只追加：已发布迁移含义不可修改；schema 变化必须验证新库创建、旧库升级、失败回滚和
重试。页面和 Service 不拼 SQL。账号库、WAL/SHM、日志、截图、PCAP、OCR 临时文件、安装器和本机
绝对路径不得提交。

### 4.1 关键持久对象

- **库存快照**：不可变 `snapshot_id`、来源、装备、词条和角色实例；当前指针只指向完整稳定快照。
- **活动配装**：保存角色、`source_snapshot_id`、assignment、payload、来源类型和锁状态。
- **方案 payload**：`assignment_scores` 保存逐件评分；`tape_main_values` 保存卡带满级主词条具体值。
- **计算保留锁**：约束后续求解和保存，不是游戏装备锁。
- **应用设置副本**：结构化账号偏好按 key 复制读写；倒带使用 `rewind_recommendation`。主题是应用全局
  偏好，不属于账号设置副本；首次升级仅从启动账号的旧主题值迁移一次。
- **装配/状态任务**：保存固定来源快照、角色项、事件、尝试次数和最终确认状态。

快照清理必须保护当前快照、活动配装和未完成任务引用的来源快照，不得使历史方案失去可复现输入。

## 5. 不可变库存快照

nte-core 事件与视觉扫描都收敛到统一 SQLite 快照模型：

```text
nte-core event ─┐
                ├→ 完整性/稳定化 → 不可变快照 → 下游固定 snapshot_id
视觉扫描结果 ──┘
```

- `InventorySnapshotStabilizer` 依据完整内容指纹和安静窗口判断稳定，不使用历史最大数量或玩家特定数量。
- nte-core 快照具有正式装备 UID、角色实例、可靠状态读写和极速装配能力。
- 视觉快照可用于仓库、计算、历史配装展示和倒带库存统计，但临时 UID 不得进入极速装配；视觉来源
  也没有可靠角色装备归属或锁定/弃置写回能力。
- `inventory.get_latest` 只读取最近捕获数据，不代表强制游戏刷新。
- 下游在操作开始前解析一次当前快照，运行中不得追随最新指针。

## 6. 计算、角色与优化器

计算在固定快照内为角色分配唯一驱动和卡带。输入包括角色顺序/平级组、套装、卡带主词条、副词条
优先级与黑名单、评分等级、暴击约束、账号基础权重、额外形状、profile 和锁快照。输出为不可变
`WeightedAllocationPreview`。

### 6.1 候选与硬约束

1. `AllocationLockSnapshot` 在候选构造前剔除全部锁定真实 UID；失效锁定阻止计算。
2. 驱动副词条黑名单最先执行且属于硬过滤；副词条自选不是硬过滤。顺序模式优先使用嵌套前缀的
   最深候选池，一致模式优先使用命中数量最多的候选池；组合无解时逐层放宽，最后回到完整候选池。
3. 卡带先执行套装和主词条硬过滤，再按同一副词条分层回退规则选择；主词条优先于副词条。
4. 默认套装为四件套，玩家显式修改时才覆盖。
5. “不限制评分等级”只取消副词条自选的评分生效门槛，不取消套装、卡带主词条和副词条黑名单硬过滤。
6. 平级组暴击恢复顺序固定为“只换卡带 → 冻结必需套装件并重选额外件 → 仅失败角色从零重配”。
   已成功同级角色不能被最后阶段释放。
7. 已分配给前序角色的真实 UID 不再进入后续候选；虚拟占位评分为 0，不能锁定或极速装配。
8. 角色拖拽跨过 `>>` 批次边界时，每个被跨过的 `>>` 反向移动一格：前往后拖拽时向前，后往前时向后；
   后往前拖拽产生新边界后，上一 `>>`（没有则从首项）至新边界前的关系统一归并为 `=`。

### 6.2 默认角色配置

角色毕业模板提供默认套装和默认专武。未显式覆盖时：

- 所有角色的卡带主词条与副词条优先级默认均为不选择，只有账号显式配置才生效；
- 升级不得删除、改写或用新默认覆盖账号已经保存的角色词条配置，包括主角「零」既有的“环合强度”配置；
- 所有角色默认携带静态毕业模板中的专武；
- 有效暴击率上限按 `100 - 满级默认专武暴击率` 生成。

公开有效配置读取器统一合并模板默认与账号覆盖。全局最优只接受显式全局覆盖，不能把角色优先模式
的默认配置误当成全局输入。

### 6.3 基础权重与动态权重

账号基础权重是持久化计算输入，未编辑账号使用发行推荐；编辑后只影响当前账号。角色页动态最终权重
依据当前面板的直伤边际归一化，未进入直伤公式的属性保留基础权重。动态权重只用于角色分析和替换
排序，不写数据库；角色页保存养成状态时也不得覆盖基础权重。

### 6.4 卡带满级主词条

计算统一忽略快照中的一级卡带值，使用 `StatCatalog` 满级值：金/橙系数 `1.0`、紫 `0.8`、蓝 `0.6`。
计算结果把具体值写入 `payload.tape_main_values`（按卡带 UID 索引）。配装卡、空幕属性汇总、角色属性
汇总和替换优先读取保存值；旧方案缺字段才调用同一 helper 回退。nte-core 与视觉快照必须同口径。

保存前复核账号、generation、快照、profile 和锁；保存只能使用原 preview，不能打开最新状态补齐旧结果。

## 7. 配装、游戏导入和计算保留锁

配装页面读取账号库活动方案。方案真实装备使用正式 UID，始终按自己的 `source_snapshot_id` 解析；
库存更新后也不能把历史 UID 改到最新快照查询。

游戏配装视图只读投影 nte-core 稳定快照。用户显式导入后才保存为活动计算配装，来源类型为
`game-observed-loadout-v1`，并冻结来源快照、逐件评分和卡带满级值。导入方案与计算方案共同参与
锁定、删除、替换、装配和倒带推荐。完整驱动但缺卡带的方案可保存为 `incomplete/missing_tape`；
视觉扫描没有可靠角色装备归属，不能作为游戏配装导入来源。

锁规则：

- 锁属于活动方案、仅当前账号生效，不调用游戏锁 RPC；
- 至少一件真实装备即可锁定，允许缺卡带；空方案和含虚拟占位的方案不能锁定；
- 锁定方案不能删除、覆盖或单件替换；批量清空跳过并报告；
- 其他角色不能借用锁定 UID；DAO 保存事务是最终防线，UI 过滤不是安全边界。

`EquipmentPresentation` 是组合根持有的唯一公共装备展示组件。它生成装备卡、评级、属性收益、差异和
结果区域，不写 SQLite、不选择当前快照、不启动 worker。计算、配装、仓库和鉴定只通过其公开接口复用。

## 8. 仓库、扫描与鉴定

### 8.1 仓库

仓库读取固定快照。`WarehouseInventoryService` 生成投影；状态管理 Service 生成并复核锁定/弃置计划；
Integration 执行写回。计划固定 `snapshot_id` 和目标 UID。RPC 接受只表示已提交，必须等待递增稳定快照
确认；超时显示待确认。视觉库存只读，不伪装成 nte-core 可写来源。

弃置/锁定管理的评估角色范围只影响状态规则评分，与计算角色、活动配装和倒带偏好隔离。仓库单件
鉴定只调用公开鉴定 Service，不持有鉴定页面或 Controller。

### 8.2 视觉扫描

扫描启动时创建不可变 dependencies，冻结账号、generation、目录、用户库和管理配置。OCR/视觉
Integration 只解析；`ScanFileLifecycle` 管理文件；只有完整结果由 `import_vision_inventory` 一次提交。
取消或异常不提交半成品。缺失卡带数值按统一满级规则补全。

### 8.3 鉴定与热键

鉴定把截图、剪贴板、手工输入和仓库单件入口统一投影为装备对象，再调用公共评分与展示。它不写库存、
基础权重或配装。扫描与鉴定共享应用级 `GlobalHotkeyManager`，但 owner 分别为 `scanning` 和
`identification`；同一时刻一个会话，owner 只能停止自己的会话，运行中的绑定冻结到任务结束。

扫描 Controller 不提供鉴定、装备展示、配装或通用热键代理接口。

## 9. 工具与倒带推荐

“工具”的推荐分析是独立只读入口。倒带推荐读取固定库存快照和活动计算配装，输出自定义奖池的八个
可重复驱动形状槽；不写库存、不修改计算 Profile 或活动方案。打开页面和修改选项不自动生成，只有
显式“生成推荐”才分析。用户显式“进行倒带”后才进入游戏输入 Integration：冻结品质、定制模式和八槽
方案。每个选中品质必须先点击对应难度，等待页面刷新后再识别右上角该难度自己的胡萝卜币；不得在
切换难度前读取余额，也不得把一个难度的余额拆分给其他品质。每个难度按自己的完整十连批次执行，
随机驱动十连固定消耗 600；“是且不做更改”先进入现有驱动定制但不修改候选，“是且应用方案”先完成
八槽配置，两种定制模式都必须在定制页面识别右侧硬币口下方的十连价格，不能使用左侧单抽价格或假定
固定价格。每个模式只点击“投币10次”，余额不足当前十连价格时保留。难度、随机/定制模式及方案配置
等前置点击后均等待 1 秒。十连循环固定为“点击投币10次 → 等待 1 秒 → Esc → 等待 1 秒 → Esc →
等待 0.5 秒 → 下一次投币10次”；执行期间 F12 可停止。

偏好 `target_character_ids`、`main_character_ids`、`strategy`、`target_grade` 保存到当前账号 application
setting `rewind_recommendation`。评分策略只读取活动计算配装，不读取图纸候选或未导入的游戏配装；
每个方案按自身 `source_snapshot_id` 解析驱动。逐件评分优先读取 `payload.assignment_scores`，只有旧方案
缺字段才兼容补算。

目标等级阈值：

```text
threshold = grade_ratio × max(1, drive_area) × 10
grade_ratio: D=0, C=.2, B=.3, A=.4, S=.5, SS=.6, SSS=.7, ACE=.8
gap = max(0, threshold - saved_assignment_score)
```

高于目标的分数不抵消其他驱动缺口。

- **全面均衡**：全部培养角色的低分驱动各占基础槽；不足八槽时按
  `形状评分缺口 / max(1, 形状库存)` 的整数比例补重复；超过八件返回提示而不伪造局部方案。
- **少角冲分**：只看冲分角色；剩余槽逐次最大化 `形状评分缺口 / (已选数量 + 1)`。
奖池每槽概率 12.5%。同形状数量 `q` 的每槽价格为 `10 + 5 × (q - 1)`，该形状总价为
`q × 单价`；8 种不同=80、4 种各 2 次=120、8 槽相同=360。初级/中级/高级只改变产物品质
（蓝/紫/金），不改变本工具的形状概率、槽位或价格，难度不进入请求。

## 10. 战报、设置与外部集成

战报由 nte-core combat 会话提供聚合事件与摘要，写账号历史；背包同步和战报不能争抢同一捕获会话。
聚合摘要不等同逐击数据，未开放的逐击、Buff/Debuff 区间、队伍和敌人实例能力不得由 UI 猜测。

设置页只接收 `AppContext`。主题写应用级全局配置，账号切换不改变主题；其他账号偏好仍写当前账号。
更新由 Mirror Controller/Integration 编排；应用不另建更新签名链。日志分层：
基础设施管理 sink，Controller 记录操作生命周期，Service 记录业务阶段，DAO/Integration 记录存储和
外部交互，Domain 返回 diagnostics。日志禁止完整 RPC、完整背包、UID 列表、账号显示名、用户绝对路径、
OCR 全文、截图、CDK、Token 或鉴权 URL。字段规范见 `docs/reference/logging-events.md`。

nte-core、Npcap、dwmapi、插件和游戏输入属于 Integration。根目录本机二进制保持 Git ignore；只有记录
上游 commit/版本/许可并完成协议、打包和真实 Windows 验证后，才可更新 `third_party` 正式发行组件。

极速装配对每个角色最多发送三次完整装配请求。每次请求均保留指令间等待，并在下一次请求前等待递增
稳定快照复核；第一次或第二次不一致时卸空重装，第三次仍不一致才生成最终报告。稳定快照等待失败时
停止后续请求。UI 必须区分插件管道当前缺失、管道存在但短暂不可用、队列繁忙、nte-core 请求超时、
稳定快照等待超时和最终装备状态不一致，不得统一显示为“管道不存在”或“装配位置不一致”。

## 11. UI 与工程约束

一级导航：工作台、计算、配装、角色、仓库、鉴定、战报、工具、设置。角色图纸和基础权重是角色子页；
二级页通过 `parent_key` 保持父导航高亮。MainWindow 只管理导航、账号切换、页面生命周期和退出。

- `src/`、`tools/` 与 `tests/` 中的 Python 文件默认不超过 800 行，按状态所有权拆分，禁止压缩代码规避。
- 版本唯一来源：`src/app/version.py::__version__`；依赖唯一声明：`pyproject.toml`；锁文件：`uv.lock`。
- 新增 `type: ignore` 必须包含错误码与原因。
- Ruff `E9/F63/F7/F821/F401` 是全仓硬门禁。
- 新增测试依赖必须写入 `pyproject.toml` 与 `uv.lock`；测试文件不得依赖当前开发环境偶然安装的包。
- Release 由维护者本地构建并使用 `gh` 手工发布；不建立自动发布工作流。
- Windows 验证器是维护工具，不进入安装包，也不代替人工游戏验证。

## 12. 文档规则

`docs/` 只保留少量面向开发者的稳定文档：

- `docs/README.md`：唯一索引；
- `docs/architecture.md`：系统边界与公共数据流；
- `docs/features.md`：现有功能原理与数据契约；
- `docs/integrations.md`：外部组件、扩展和二进制边界；
- `docs/roadmap.md`：未完成事项和上游依赖；
- `docs/reference/`：公式与字段规范；
- `docs/validation/`：真实环境验收。

功能变化时直接覆盖失效描述，不追加“本次修改了什么”的流水账。界面文案、颜色、间距等微调不写技术
文档；只有数据结构、所有权、生命周期、算法、外部协议、跨功能边界和仍影响开发决策的存量例外值得
长期记录。例外解决后删除对应说明，过期计划直接删除或压缩进 `roadmap.md`。

## 13. 验证入口

| 改动 | 最小验证 |
| --- | --- |
| AppContext、账号、后台任务 | app-context、account-user-database |
| 同步、快照、视觉扫描 | inventory-sync、stabilizer、vision、streaming、OCR golden |
| 计算、默认配置、替换 | allocation、weighted-allocation、role-selector、replacement |
| 配装、锁、游戏导入 | loadout DAO、lock、game-loadout、equipment display |
| 角色、权重、图纸 | official-role、character-weight、blueprint |
| 仓库、鉴定 | warehouse、identification、hotkey boundary |
| 倒带推荐 | rewind-shape-recommendation、toolbox-page、saved-assignment-scores |
| 自动装配 | equipment-apply、drive-assembly、bulk apply |
| SQLite/静态数据 | migration、static-data、manifest |
| 文档/结构 | Markdown 本地链接、dependency-boundaries、repository-hygiene |

权威命令：

```powershell
python tools/quality/run_tests.py core
python tools/quality/run_tests.py full
python -m mypy <tools/quality/mypy_allowlist.txt 中列出的文件>
python -m ruff check .
python -X pycache_prefix=build/compile-cache -m compileall -q src tests tools
python -m uv lock --check
git diff --check
```

专项测试、静态检查和 full 中出现的已知存量失败必须单独列明，不能用它掩盖新增失败，也不能把“除已知
例外外通过”表述成全量通过。修改触及已知例外时，至少保证失败集合不扩大；改变产品硬契约时必须先让
对应公共行为测试明确表达新规则。

涉及真实游戏、驱动、插件或更新时，再执行 `tools/windows_validation` 和
`docs/validation/windows.md`。完成前检查静态库哈希未被意外修改、所有新文档链接有效、无本机数据进入
Git，并确认上游与下游公共行为均通过。
