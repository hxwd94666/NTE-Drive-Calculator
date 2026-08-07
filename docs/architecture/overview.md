# 2.0 架构说明

## 数据边界

应用把数据分成三类 SQLite 文件：

- `data/game_static.sqlite3` 是随版本发布的只读静态数据库，由开发者从已准备好的游戏官方文件生成。程序运行时不修改它。
- `app_shared.sqlite3` 是本机全部账号共用的覆盖数据库，当前保存额外形状配置。删除覆盖即恢复发行默认。
- `accounts/<account_id>/user_data.sqlite3` 是每个账号独立的用户数据库，保存同步设置、不可变背包快照和装配方案。

静态表和用户表都保留游戏使用的 `character_id`、`item_id`、`suit_id`、`geometry`、`property_id`。新服务不先转换成旧项目的显示名称；界面名称只在展示边界解析。

公共可编辑值按 `app_shared` 覆盖、`game_static` 默认的顺序读取。静态 DAO
统一使用 SQLite `mode=ro`；共享覆盖只能经 `SharedDataDao` 写入，页面不直接执行
SQL。安装升级前会把旧静态库保存在 `migration/game_static.previous.sqlite3`，
首次启动依据已知旧发行基线只迁移真实的用户差异，迁移完成标记与差异写入处于同一事务。

## 背包同步

`src/services/inventory_sync_service.py` 在应用生命周期内复用一个 nte-core 进程。事件回调只替换单槽内存队列中的最新完整事件，SQLite 写入由工作线程完成，因此持续事件不会堵塞协议读取线程。

`src/services/inventory_snapshot_stabilizer.py` 通过完整内容指纹和安静窗口判断稳定：

- 不假设任何固定背包数量；
- 数量不变但内容变化时重新计时；
- 连续重复事件不会延长等待；
- 后续新增或移除装备会形成新的不可变快照；
- 同一核心会话内忽略倒序 generation/sequence。

计算开始时必须固定 `snapshot_id`。后台可继续同步新快照，但当前计算通过 `UserDataDao.list_inventory_items(snapshot_id)` 读取原输入，直到任务结束都不会漂移。

用户库默认保留最近 20 份稳定快照。当前快照和任一已保存装配方案的
`source_snapshot_id` 始终受保护；同步服务会在新快照提交后清理其余历史快照。
设置页和 `tools/user_data/manage_user_database.py prune-snapshots` 都可调整或执行
维护。清理会级联移除对应物品与词条记录，但不会修改装配方案。

## 配装计算

计算页固定从 `user_data.sqlite3` 读取当前稳定快照，并将它仅在内存中投影给既有求解器；不会再回退读取旧背包 JSON。弃置状态只作为结果展示的红色标签，驱动和核心仍参与候选计算。计算完成后，每个有效角色方案都以官方 `character_id`、原生 UID、目标坐标和 `source_snapshot_id` 保存到 `loadout_plan`。`equipped_state.json` 只保留给尚未迁移的旧页面展示，不再是计算或 nte-core 装配的数据源。

2.0 的首个新入口是 `src/services/sqlite_loadout_optimizer.py`：

1. 从静态数据库读取角色的官方装备蓝图；
2. 固定用户数据库中的一个稳定快照；
3. 根据核心 `suit_id` 和套装 `required_shape_ids` 保证必要形状激活套装；
4. 用官方 `property_id` 权重给候选装备评分；
5. 保存含原生 UID、目标行列和 `source_snapshot_id` 的方案。

当前入口是“官方固定蓝图 + 套装约束 + 属性权重”的可复现基线。后续自定义布局、全角色竞争分配、战斗模型和属性边际收益，应继续消费同一官方 ID 输入，而不是复制一套名称映射。

词条配装页面在启动 worker 前构造不可变 `WeightedAllocationRequest`，固定账号数据库、
稳定快照、配置版本和求解参数。worker 返回的 `WeightedAllocationPreview` 同时携带求解
结果、固定 `AllocationContext`、静态数据集引用和角色展示详情；结果页只消费该 preview，
不再为渲染重新打开可变账号数据库。账号切换后通过 token 和数据库路径丢弃旧 worker
回调。结果中的装配操作只调用装备功能公开的 `request_equipment_assembly` 接口，不导入
库存页面内部方法。

## 一键装配

`src/services/equipment_apply_service.py` 只接受已保存的 SQLite 方案。调用前会检查：

- 背包同步处于稳定监听状态；
- nte-core 握手包含 `equipment` 能力；
- 当前快照与同步状态一致；
- 角色、核心和驱动 UID 合法且仍存在；
- 驱动坐标位于 1–5，且方案不依赖协议不支持的旋转。

派发 `equipment.equip_one_key` 后，服务会等待比装配前更新的稳定快照，再核对角色 UID、角色 ID、核心和每个驱动的锚点位置。这个同步方法应从界面工作线程调用，不能阻塞 Qt 主线程。

nte-core 0.3.6 的装备 RPC 通过 `nte-mods-plugin` IPC v7 派发。应用部署
`dwmapi.dll` 前会把同一上游发布中的 `nte-mods.enabled`、`equipment.nte` 和
`combat-clock.nte` 准备到可写数据目录，并注册
`HKCU\Software\NTE DPS Tool\Mods Plugin\Workspace`。更新内置脚本时只替换仍与
上次托管版本一致的文件，保留用户编辑内容。

批量装配在第一条 RPC 前固定一个 `snapshot_id`，从当前活动的 SQLite 方案取得角色 ID 和装备 UID，并缓存角色实例 UID。期间即使后台收到新背包快照，也只能使本次预检查失败后重新开始，不能把不同版本的装备混入同一批装配。

同步快照中出现的 `character_id` 与原生实例 UID 会写入用户库的
`character_instance_mapping`。角色当前没有任何已装备物品、或映射不唯一时，界面
要求用户选择官方角色 ID 并输入 `slot,serial`；确认后的映射仅保存在该账号的用户库。

批量 nte-core 装配由无 Qt 依赖的 `BulkEquipmentApplyService` 编排，并持久化为任务、
逐角色步骤和事件日志。每个步骤记录尝试次数、前后快照与失败原因；失败只停止当前角色，
已确认步骤不会重发，可从失败任务继续。全部角色下发后等待新稳定快照，发现遗漏时只补装
对应角色并等待第二份快照复查。任务源快照受清理策略保护；回滚不属于当前阶段。

游戏界面自动装配是视觉方案的兜底链路。控制器显式传入应用级角色模板目录和当前账号
过程截图目录，桥接层负责角色识别、计划投影及输入动作，不读取 runtime 路径镜像。
极速装配或自动装配 worker 活跃期间阻止账号切换，避免旧账号任务或结果落入新账号界面。

## 界面与生命周期

主页是状态工作台，不是执行页。导航定义集中在 `src/ui/navigation.py`，主页页面位于 `src/features/home/page.py`。

侧栏保留工作台、计算、配装、角色、仓库、鉴定和设置七个一级入口。鉴定承载截图、
剪贴板和手工输入工作流，仓库卡片仍保留单件就地鉴定。角色图纸与基础权重从角色进入，
并提供直接返回角色页面的按钮。二级页仍在同一 QStackedWidget 中显式注册，并通过
`parent_key` 保持父级侧栏高亮；应用不提供普通/专家模式切换。

`src/app/context.py` 是账号状态的权威来源，集中保存应用级路径、不可变的当前
`AccountContext`、三类 DAO 工厂和账号设置服务。账号切换固定执行：

1. 停止账号绑定的后台服务；
2. 替换当前账号并递增 context generation；
3. 重建账号设置和已注册服务；
4. 同步通知 UI；
5. 仅恢复切换前正在运行的服务。

nte-core 同步通过显式生命周期适配器注册到 AppContext；页面只订阅状态，不直接
管理核心进程。`src.app.runtime` 和 `src.ui.app` 中的同名账号路径当前仅是待纵向
迁移页面使用的兼容镜像，不再是账号状态的独立事实源。

视觉扫描与单件鉴定在任务启动时分别创建不可变的
`ScanningDependencies`、`IdentificationDependencies`，把账号截图目录、配置目录和
用户数据库路径固定后再传给 worker。OCR worker 只返回解析结果，截图移动与清理由
`ScanFileLifecycle` 处理，人工补录完成后才由 `import_vision_inventory` 一次提交视觉
库存快照。扫描或鉴定运行期间禁止切换账号。

MainWindow 的功能面统一由 `FeatureMainWindowMixin` 在类定义时显式组合；
旧动态方法安装器和运行时 `setattr(window_cls, ...)` 挂载已删除。
兼容 facade 只能显式转发稳定入口，不得遍历模块全局对象复制可调用方法。

跨功能复用的驱动矩阵拆分规则位于 `src/domain/drive_layout.py`，装备锁定/弃置
规则位于 `src/domain/post_actions.py`，状态评估编排位于
`src/services/post_action_evaluator.py`。`services` 不得反向导入 `features`，
该边界由 AST 测试固定。

轻量 UI 图片位于 `assets/game_ui`，运行时通过
`src/services/game_ui_asset_catalog.py` 按官方 ID 查找。生成脚本会缩小尺寸并按内容
去重，避免把大尺寸源图直接装进安装包。

## 发布资源

`build_exe.py` 会把以下运行资源放入 PyInstaller 的 `_internal`：

- `src/storage/sqlite/schema` 全目录；
- `data/game_static.sqlite3`；
- `data/manifest.json` 和 `data/migrations` 的旧发行差异基线；
- `assets`；
- 固定上游 Release 的 `nte-core.exe`；
- 与该 Core 匹配的 `nte-mods-plugin` `dwmapi.dll` 和 `plugins/` 工作区模板。

`build_installer.py` 在生成 Inno Setup 脚本前会校验主程序、核心组件、Mod
插件、装备脚本、数据库结构、静态数据库、数据清单和迁移基线。正式构建应使用
合作项目的固定 Release 产物及配套许可证文件；发布预检会校验实际数据库的
dataset、schema、SHA-256 和 payload 省略状态与 `data/manifest.json` 一致。

## 质量门禁

默认测试控制台只保留 WARNING 以上日志，运行时日志文件仍保留 INFO 诊断；Windows
标准输出强制 UTF-8。Ruff 对全仓执行语法、明显错误、未定义名称和未使用导入检查。
AST 类型注解门禁从 AppContext、不可变 feature dependencies、纯 domain、新批量装配
service 和共享 DAO 开始执行，强制其公开函数具备完整参数与返回注解；后续迁移模块只有
在现有范围全绿后才扩大检查清单。

OCR 布局回归数据位于 `tests/fixtures/ocr_layout_golden.json`，覆盖 1080p、2K、4K、
16:10 与常见 DPI，并断言词条筛选和候选聚类结构。该数据为人工构造，不含账号名、
本机路径或真实玩家截图。

仓库质量测试阻止新增超过 10 MiB 的受 Git 管理文件，并检查账号数据库、日志、扫描
截图、安装包输出和 SQLite 临时文件仍被忽略。超限发行资源需先评估 Git LFS 或外部
版本化产物；历史体积清理必须独立执行，不在常规功能提交中重写 Git 历史。
