# 系统架构

本文描述当前稳定结构与公共数据流。强制开发门禁见根目录 [`AGENTS.md`](../AGENTS.md)，业务细节见
[功能原理](features.md)。

## 1. 总体数据流

```text
nte-core 完整库存 ─┐
                    ├→ 完整性与稳定化 → 账号不可变快照 → 冻结计算上下文
视觉/手柄全量扫描 ─┘                                      ↓
                                                     配装预览
                                                        ↓
                                               指定角色配装槽位保存
                                                        ↓
                   nte-core 极速装配 / 游戏界面自动装配 / 仓库状态写回
                                                        ↓
                                               后续可观察状态确认

nte-core 残缺状态事件 → 固定完整快照中已知 UID 的运行时状态覆盖
nte-core 抓包物品观测 → 同账号独立稳定观测快照 → 养成计算器草稿
```

任何下游操作都在开始时解析一次当前快照并冻结 `snapshot_id`。历史方案按自己的
`source_snapshot_id` 读取；运行中出现新快照只用于副作用确认，不改变原请求输入。
抓包物品观测虽随背包同步稳定保存，可关联同一会话的完整背包快照，但其
`complete=false` 不推进正式库存当前指针；停止监听后仍由账号库只读供养成草稿使用。

## 2. 分层与依赖

```text
UI Page / View
      ↓
Controller + frozen dependencies
      ↓
Application Service
   ↙                 ↘
Domain / Optimizer       DAO / Integration
                              ↓
                    SQLite / nte-core / 文件 / OCR / 游戏输入
```

| 层 | 拥有的状态 | 排除的职责 |
| --- | --- | --- |
| UI | 控件值、选择和可丢弃展示状态 | SQL、协议、业务算法、其他页面状态 |
| Controller | worker、取消 token、忙碌态、结果投影 | DAO 查询细节、其他 Controller |
| Service | 冻结请求、业务编排、事务边界 | Qt 页面、MainWindow、全局账号查找 |
| Domain/Optimizer | 不可变输入上的纯规则和求解 | SQLite、Qt、日志、进程 |
| DAO | schema、迁移、SQL、事务 | UI、外部协议 |
| Integration | nte-core、OCR、文件、鼠标/手柄、插件和进程 | 评分与保存策略 |
| Observability | sink、脱敏与操作关联 | feature、Service、DAO 和 UI |

`src/ui/app.py` 是 GUI 组合根。`AppContext`、`EquipmentPresentation` 和 `GlobalHotkeyManager` 经启动组合或窗口组合创建
并显式注入。MainWindow 的目标职责是导航、账号切换和生命周期；当前尚有功能 mixin 和窗口字段消费者，
按[架构重构方案](roadmap/architecture-post-refactor-review.md)逐批接管。新增 Feature 使用明确依赖，不照搬遗留字段扫描或页号定位。

启动准备由 `src/ui/application_runtime.py::prepare_application_runtime` 显式组合，返回同一运行期的
`AppContext`、账号管理器及共享本机 store 上的主题、语言服务；语言目录在 Qt 创建前冻结。`MainWindow(runtime)` 消费这些依赖，不从模块级默认账号取得状态。
路径解析与可写探针归 `src/integrations/application_runtime_paths.py`；权限确认先于账号准备，Qt 入口随后消费窗口工厂。
普通导入不初始化账号、连接 SQLite 或设置 NTE 路径变量；日志导入只准备控制台 sink，账号文件 sink 由显式日志生命周期创建。
GUI 启动由 src/ui/gui_startup.py 在 QApplication 创建前显式保持原 Windows SystemAware 初始化；不再依赖鼠标库导入的隐式进程状态变化。
鼠标实现只在正式后端构造时加载，可显式注入 GUI driver；取消与异常后的释放仍由原 guard 和 owner 收尾。
角色优先、同级、候选排名及预留恢复的线性匹配依赖只在实际求解处加载，执行仍在既有 worker，
冷加载前后复核取消；矩阵、候选顺序、UID 唯一性及恢复算法不变。按需加载不意味着可从发行包删除运行依赖。
一次启动工坊刷新由 `src/ui/controllers/workshop_refresh_controller.py::WorkshopRefreshController`
拥有窗口父级的单次延迟 `QTimer`、不可复活取消 token 和一个后台线程；关闭停止定时器并撤销 token，
主窗口既有非阻塞退场等待线程物理结束，不在 GUI 线程 join 或重发。模板是本机公共输入，不随账号切换重启。
`src/integrations/workshop_weight_http.py` 只在实际后台请求时加载 QtNetwork，局部网络对象和事件循环
归该线程，以有限总时限、取消及响应体积门禁读取，DeferredDelete 在创建线程收尾。
无请求的冷启动不加载 QtNetwork；按需导入不表示发行可裁除，构建与精确裁剪仍保留对应运行输入。
隔离证据、实际应用综合烟测及真实 TLS／发行待验收范围见[首批架构验收](validation/architecture-renewal-day1.md#29-当前验证边界与剩余门禁)。

性能采样的外部边界归 `src/integrations/performance/`：帧采样与 Windows 会话寿命各自成模块，
两类有界诊断日志共用编码/容量写入实现，本机偏好单独保存。`src/services/performance/` 拥有监控与细分采样任务，
不改变既有连接、账号代次或租约权限；日志构造失败由构造者释放已打开的文件流。

### 2.1 角色配置领域入口与测试发现

`src/services/characters/` 持有基础权重编排、账号权重、自建角色、额外形状及工坊模板。
消费者显式导入具体模块，包入口不聚合评分器、战报或 UI；原平铺模块已删除。
角色创建与多字段提交继续复用 DAO 单一事务，没有新的事务框架或兼容转发。
工坊默认值按一次批量读取、保存或重置冻结模板内容与版本，批内复用、请求结束释放；不建立常驻全局缓存。

官方角色的纯等级／突破与毕业形状规则、家具值模型归 `src/domain/official_role/`；
正式装备属性聚合归 `src/domain/equipment/official_stats.py`，不混入评分公式。
`src/services/official_role/` 分别拥有面板、档案投影、觉醒、毕业解释、库存来源、基础评分、替换边际和弧盘模板；
基础权重评分适配与替换动态权重各守现有口径，不通过页面聚合导出。
资料图片定位及旧输入指纹归 `src/integrations/official_role/read_inputs.py`；
正式发行的文件与字节校验归 `src/integrations/static_catalog_release.py`，DAO 独占 SQL 和一致读取事务。
替换弹窗编排归 `src/features/official_role/replacement_controller.py`，角色与配装调用其公开入口。

`src/services/official_role/page.py` 持有索引、详情和页签顺序 owner，旧平铺页面模块删除；
`src/services/official_role/profiles.py` 保留真实保存、重置与原生档案补丁事务 owner。
子包仅声明职责，消费者直达具体模块，不聚合导出或保留转发壳。

库存同步编排与事务归 `src/services/inventory/sync/service.py`，冻结状态与时钟归同包 `state.py`；
公开同步 DTO 保留原 owner，不靠薄转发修复注解。游戏状态观测与自动同步预检分别归
`src/services/work_modes/{observation,sync_preflight}.py`，读会话、模式和任务权限仍独立，自动同步不附带写操作。

详情和多角色组装通过既有发行 Reader 的有界读取单元：Windows 本机固定磁盘的普通、非重解析文件
从流式 SHA-256 校验前到 fresh 只读 SQLite 事务关闭后持续持有仅共享读取的文件 pin。
数据库字节、大小与正式清单匹配，并在同一专属 DAO 校验 dataset/schema/importer/scope；
WAL 模式、WAL/SHM 或恢复日志输入拒绝进入发行读取，不检查点或清理账号活库。
多角色借用同一个 owner，所有静态值在释放前物化；专属 DAO 收尾不摘除别人的共享连接注册。

SQL 查询归 `src/storage/sqlite/catalog/static_game_data_queries.py`，runtime DAO 仍独占只读连接、事务和共享注册寿命。
静态构建使用独立 `StaticGameDataBuildDao`，只在构建 owner 的现有事务中借用原连接读取未提交事实；
借用 reader 只关闭自身 cursor，不开启/结束事务，不关闭连接或操作共享注册。
`src/services/official_role/detail_inputs.py` 物化角色事实，`detail_model.py` 纯值装配；runtime 与构建共用面板和既有公式。
正式 runtime 页面入口仍完整核验发行清单/pin/字节并单独附 `static_release`；构建详情无此字段，也不进入 runtime 请求缓存。
毕业模板只消费候选静态推荐，事务成功或失败仍由原构建入口提交或回滚，之后经正式构建清单与晋升认证。


详情顶层运输该次已验证的 `static_release` 字节身份及显式路径/schema。
按需库存补全和替换满级投影复核同一 token，旧资料更新后在静态查询前终止，不能重新解析默认来源。
补池与投影共用一次读取单元，纯排名在 SQLite/pin 物理关闭后执行；保存只消费冻结分数并在原提交边界复核 token。
局部请求缓存用已验证字节身份区分弧盘、属性、技能渲染器等资源，失败不发布本次新缓存，
不把旧路径缓存重新标为当前发行。legacy 多角色图纸与战报编辑器详情组装复用同一单元。
这些运行期来源值不进入档案、方案、原始战报、分享或导出格式；图鉴与战报 dataset 的用途边界保持。
普通与加权分配的 `static_release` 来自本次正式读取单元，贯穿面板物化、纯求解、交付及保存。
大小、修改时间和 dataset_id 只保留为元数据，不能作为保存许可；计算中资料更新丢弃本次结果，
保存前更新拒绝旧预览，缺完整发行身份的旧预览要求重新计算，不补造当前来源。

`tools/quality/run_tests.py` 统一用 pytest 原生执行函数和 `unittest.TestCase`。
`core` 保持模块标记／文件名规则，专项 pattern 保持显式范围；并行按真实收集结果整文件分片。
收集错误、子进程失败及零测试保留非零状态；养成函数式用例直接发现，旧 `load_tests` 子进程桥接已删除。
基础权重与自建角色关键集合归 `tests/characters/`，官方角色保留集合归 `tests/official_role/`；
层级、资源定位和动态 patch 目标随各自归包更新。测试按关键行为保留，不为旧路径保留生产兼容入口。
历史检查边界见[首批验收](validation/architecture-renewal-day1.md)，当前整改与验收范围见[复审方案](roadmap/architecture-post-refactor-review.md)。

### 2.2 仓库读取领域与状态投影

仓库 UI 原语与读投影归 `src/features/inventory/warehouse/`；纯筛选及读取 DTO 归 `src/domain/inventory/`，
应用服务归 `src/services/inventory/`，游戏状态协议写适配归 `src/integrations/inventory/`。
各包不聚合重导出，消费者直接导入具体模块；配装页面与同步 owner 不因此混入仓库读取类。

`WarehouseReadController` 复用 `PageTaskLane`，跨账号保持一个运行任务与一个最新请求，忙时不丢掉刷新。
请求冻结账号、代次、用户库、主静态路径与素材根；切换前通过 `AppContext` 生命周期撤销读取交付并清除旧投影。
关闭先失效通道，再由现有页面任务关闭流程等到物理终态，不提前销毁运行中的线程。
后台读取 callable 只冻结读取函数与请求，不反向捕获 Controller；取消后延迟删除不能靠保留 owner 引用维持对象寿命。
读取失败保留同账号有效面板；账号切换已经清空的面板不从旧结果补回。

交付时再次核对草稿及状态操作占有：读取期间发生的锁定/弃置编辑不被覆盖，保留待刷新身份，
撤销草稿或交付状态结果后只排一个重新读取。延迟回调带 QObject 生命周期和账号代次门禁。
状态业务忙以公开投影标记到结果处理终态，包含弹窗嵌套事件循环，不把结果已交付后的物理线程收尾误作仍在写入。
未获新后验快照的接受投影设置确认边界，旧快照不覆盖待确认状态；后续新同步证据才解除。
主读取与视觉卡带路径回退显式传入冻结来源；其余绘制/筛选全局回退和旧外部写 owner 仍待后续收敛。

仓库保留验证归 `tests/inventory/`，旧路径、相对导入、动态 patch 和质量 allowlist 同批迁移；
纯像素颜色及源码字符串两例删除，来源/状态/后验确认关键验证保留。实际证据见[首批验收](validation/architecture-renewal-day1.md)。

### 2.3 配装领域与纯身份规则

配装选择、预览、展示、分享及装配页面 Controller 归 `src/features/inventory/equipment/`；
仓库页面入口归 `src/features/inventory/warehouse/controller.py`，不再由旧 inventory 页面反射聚合。
配装编排归 `src/services/equipment/`，正式装备身份、虚拟占位、方案冻结分数、套装激活及分享规则归
`src/domain/equipment/`；纯库存来源能力归 `src/domain/inventory/source_capabilities.py`，DAO 不反向依赖 Service。

分配库存投影、账号筛选、方案锁、普通保存、冻结比较评分、槽位比较与加权保存归 `src/services/allocation/`。
普通方案和加权预览的卡带主词条投影分别由 `plan_save.py` 与 `weighted_save.py` 持有，不另设小型转发模块。
普通保存保留 checkpoint、静态身份/锁复核及其 DAO 事务；两种 writer 与战报导入／导出共用 Integration 的 `static_catalog_commit_unit`，
先以成熟 Windows 只读 pin 固定同目录发行清单，再进入同一 release 的正式读取单元；退出顺序相反，
覆盖最终核验、DAO commit 与 reader 退出，避免已经提交后才发现清单改变。账号提交门闩、锁、取消和两种事务语义不合并；战报转移不把历史原始事实的 dataset 改成当前发行。
加权保存的账号任务及最终事务门禁见[加权保存责任](#214-加权预览的程序化保存与物理退场)。
评分器与槽位比较分离，避免设备比较服务形成依赖环；两个保存事务不合并。
包入口仅中文职责注释，不聚合重导出；生产 import、动态 patch 与质量路径直接指向具体模块，原八个平铺入口已删除。
分配输入、目录与既有适配责任见[分配输入章节](#215-分配输入目录与失败证据)；桌面正式执行与程序化加权入口的边界见[加权责任](#214-加权预览的程序化保存与物理退场)。
普通桌面计算及保存线程归 `AllocationController`；关闭先撤销新任务与迟到交付，再由扫描 owner 向现有窗口定时门禁报告物理运行态。
窗口等普通计算／保存线程真正结束，不在 GUI 同步等待五秒，也不把取消请求当作物理退场。
关闭后的保存保留已提交回执，抑制迟到渲染和成功／错误模态；不回滚已提交事实或自动重发写入。

配装页清空、删除、槽位管理与计算锁由显式 `EquipmentPersistenceController` 持有后台读取和单次提交，
`LoadoutManagementService` 冻结账号代次、用户库路径、正式角色/槽位及方案来源/锁；名称只作展示。
DAO 在同一写事务内复核冻结范围后修改槽位/指针，清空未锁定方案为整批原子事务，保留历史来源与逐件分数。
任务提交不可自动重发；页面确认异常与事务失败分开，账号切换/管理/关闭先撤权并有界等待物理退场。
超时不视为任务结束，迟到回调不更新新账号；槽位对话框写入后只发起新读取，不复用已提交token。
单角色与一键游戏方案导入也复用该 owner 的准备／确认／提交通道，只消费准备阶段冻结的投影及逐件评分。
导入统一调用成熟 `save_calculated_loadout_plans` 当前槽位事务，目标槽位创建、方案锁、UID 借出及虚拟残留同时处理；
旧历史方案的来源与冻结评分不修改。可选提交上下文只保护末端检查与 commit，不另造借出算法。
普通计算保存与外部装配仍由各自 owner 管理，不混入上述本机配装操作。

展示标签与来源策略徽标集中在 `equipment/plan_projection.py`，未知正式属性保留既有原标签回退。
真实 UID、slot_id、方案来源快照与冻结逐件分数不随目录调整改变；分享仍由正式 ID 关联公共素材。
本次生成的变动对比使用冻结计算口径，旧方案历史分数不被当前权重重解释，虚拟占位保持零分。
已无生产消费者的固定推荐蓝图 Python 求解入口及旧 roles_db/workshop_item_id 身份链删除，
正式分配、保存与角色解析继续使用冻结上下文及静态角色身份，不引入另一套评分公式。
装配外部写 owner、同步会话及游戏协议未在此批重写，页面窗口字段消费者仍按后续业务边界收敛。

配装保留验证归 `tests/equipment/`，分配首片八模块归 `tests/allocation/`；跨域测试只迁生产引用。
共享比较夹具随本域消费者迁移，其余跨域夹具来源保持；库存投影显式标为 core，八模块均进入默认门禁。
实际有界验证、重复参数删除与未覆盖范围见[首批验收](validation/architecture-renewal-day1.md#8-配装领域归组与无消费者旧入口清理)。

### 2.4 本机偏好与文本目录

`src/services/preferences/` 按本机偏好归组主题与语言编排，原平铺主题入口和旧引用已迁移，不保留转发壳。
`src/domain/localization/catalog.py` 只校验纯文本目录、参数及 locale 身份，产生不可变目录；
`src/integrations/preferences/` 独占有界文件读取、补丁路径、坏 JSON 归一化与原子替换。
同运行期共享一个 `LocalPreferencesStore`，合并字段不靠缓存整份配置回填；不承担多进程配置同步。

组合根通过 `ApplicationPaths` 提供发行目录及本机补丁路径，并注入共享 store 和 Service。
语言 Service 冻结当前目录、缓存可选项和下次启动偏好，卡片渲染不访问磁盘。
`LanguageController` 拥有 `PageCommitLane` 及冻结操作参数，卡片仅拥有控件、纯文本提示与关闭等待投影；
后台 callable 不反向持有视图 QObject，提交不会被取消、合并或自动重发。

界面目录不进入评分、账号库、OCR 模型选择或扫描词库。识别输入配置的后续边界见
[语言预备方案](roadmap/architecture-post-refactor-review.md#鉴定与扫描的功能内语言预备)，当前产品行为见
[界面语言补丁](features/localization.md)。

### 2.5 视觉输入与共用识别

`src/integrations/vision/window_capture.py` 负责窗口内容坐标、截图和边框裁切；
`src/integrations/vision/recognition/` 归组批处理、OCR 后端、模型校验、形状模板、装备分类、
文本解析、鉴定聚类与增量去重。包入口不重导出引擎，构建工具可仅加载模型校验而不初始化 OCR。
扫描、图片鉴定、装配 OCR 与倒带通过同一正式入口消费相关能力，不留旧路径兼容壳。

`BatchProcessor` 持有本批引擎、成功截图和增量签名缓存。四个生产批处理入口必须传入
`RecognitionResources`；`services/recognition/resources.py` 用新鲜静态 DAO 的一致读取事务冻结
实际 dataset、套装及形状，保存所选 stats 原始字节与 SHA-256。DTO 只含不可变数据与纯解析；
词条目录由每消费者私有构造，不逐张截图重新读取。中文输入身份不由 UI 语言推断。
图片处理器另冻结 `RecognitionLayout`、实际解码的 `ShapeTemplateFile` 与 `OcrRuntimeResources`，
在引擎成功构造/预热并核对实际 CTC 字符顺序后，作为 `RecognitionImageProfile` 附到同一资源对象。模板从同一份前后版本一致的字节计算哈希并解码，基础/缩放矩阵只读；
尺寸缓存只归本处理器，按正式形状与裁切尺寸复用，4 MiB 为缓存持有像素预算，不是进程峰值。
普通截图和强制鉴定共用该布局，裁切坐标、匹配顺序、插值与阈值不变。显式重载先构建候选，
已有池必须保持必需模板完整，失败保留旧池/证据/缓存；完整替换才清缓存。

鉴定任务 owner 将原资源从图片解析带到主词条确认及匹配；Service 和实际图纸 DAO 复核资源/数据身份，
评分消费冻结词条。手工和文本剪贴板在数值解析前冻结资源，显式词条目录与随后匹配共用同份身份，
不从页面旧评分器读取解析规则；派发前复核账号代次，资源中途变化时拒绝匹配，保留输入及已有结果。
已有结构化仓库物品直接匹配时独立冻结一次，不借用其他图片任务的资源。
`services/inventory/vision_snapshot.py` 拥有视觉投影和导入，`visual_scan_commit.py` 拥有完整数量/失败门禁。
程序化 facade 同样经过该入口；单图错误后的非空集合不代表完整库存。完整图片结果还必须携带
包含必需形状及完整 OCR 资源的图片 profile；确认/匹配及最终事务授权复核模板、模型和字典文件，
不在执行中重绑布局或新资源。公开身份只含内容哈希，不把本机路径、字典原文或推理对象写入库存 metadata。
手工匹配和已有结构化库存导入只需静态/词条资源，不为了这一门禁启动 OCR 或加载模板。
导入在写入前及事务内最终授权点复核，资源/代次/取消失效回滚快照与当前指针；授权点不是外部文件事务锁，
授权后变化也只允许已冻结的完整结果完成，不改用最新资料。

文件版本及 WAL 仅为变更探针，不能替代 stats 哈希或实际 DAO dataset 核对；发行静态只读及原子晋升前提下
不逐图哈希大库。独立 Parser/ShapeRecognizer 的真实调用者保留原构造入口，批处理不缺省回落。
`recognition/ocr/backend_runtime.py` 独占执行后端选择、有限设备探测、原生初始化与预热；`ocr_engine.py`
只持有实例、共用灰度转换和结果投影。偏好从参数/环境解析一次，OpenVINO、CPU 及低负载路线不探测显卡或 ORT providers；
DirectML/auto 才有限探测，确认独显后才查询 provider。每个原生候选构造前、构造后及预热后校验
同一份模型/字典；两个后端均显式传入共享 `rec_keys_path`，实际 CTC 全部字符顺序（含 blank/space）必须一致，参数字典私有；
取消或资源漂移直接结束本次初始化，不作为备用后端理由。DLL 搜索路径按实际加载后端注册、去重并保留句柄至进程结束，
模块导入不修改 root logger 或注册 DLL。低负载限制为进程级尽力设置，不保证已加载的其他原生会话重新读取。

鉴定与两个解析 worker 把普通取消 Event 传到批处理及 OCR；停止热键按实际运行态选择任务并调用同一个 worker 取消入口。
原生构造/推理仍不可抢占，返回后复核取消，不进行备用初始化、重试解析或交付；物理 owner 仍等线程退出。
单图推理前后检查内存 CTC 顺序，不逐图重哈希模型；资源/接口错误贯穿鉴定、奖励回退、批处理与离线 worker，
流水线停止消费失效引擎并排空未开始任务，错误结果不交付成功或触发后续动作。文件身份仍在业务授权阶段复核。
`recognition/ocr/task_session.py` 为装配/倒带同步任务提供惰性 `OcrTaskSession`，嵌套导航、角色及
动作显式借用同一 owner；独立任务重建，不保留全局引擎。首次角色识别、装配目标及倒带多品质
余额/价格复用本次实例。纯裁剪、缩放和文字匹配归 `recognition/ocr/interaction_ocr.py`，Feature 保留业务点击选择。
初始化失败使任务失效，取消/资源故障不回退坐标；普通目标未匹配及普通图片解析失败仍保留原回退。
原生预热/推理返回后才离开同步作用域，释放自有引用；注入引擎不被关闭，不声称原生句柄立即销毁。
装配后端 finally 覆盖首次识别到末次输入和中间异常。倒带执行以 backend is None 区分所有权：
自建后端创建成功立即纳入 finally，正常、拒绝、取消、识别或输入失败均先 release 再 close；
release 出错仍尝试 close，主故障保留并附独立收尾错误，正常路径收尾失败也报故障。
借入后端（包括假值对象）不被替换或收尾；倒带 UI 通过 `PageCommitLane` 独占其 release/close。
`ToolboxPage` 持有倒带对话框；为游戏让出前台的 hide/exec 返回不等于关闭。确认与派发复核初始账号代次，
关闭先撤销选择和输入，再等后端、热键及线程物理收尾；组合根在运行及收尾期间阻止账号切换。迟到结果不重开旧窗口。
倒带推荐只经 `services/rewind/recommendation.py` 读取一次冻结库存及明确所选槽位；
`domain/rewind_loadout.py::inspect_slot` 纯校验保存锚点、正式形状和逐件历史评分。
每个一致读取作用域只构造一次形状索引，来源缓存按 source_snapshot_id 与所选 UID 集隔离；
当前库存数量不能补齐来源方案。未知分数不作零，slot_id、方案来源及原保存评分不跟随当前默认值变化。
初始捕获或识别作用域进入失败若早于后端创建，不为收尾提前创建资源；缺显式 OperationGuard 仍拒绝新输入。
同步原生识别物理返回前不关闭 owner，返回后取消拒绝后续输入。MSS 自建用 with，借入不关闭。
非 OCR 旧截图/重试策略不据此宣称全局完成。OCR 支持声明、依赖组与消费者选择已接管，验收界限见
[OCR 支持集合](validation/architecture-renewal-day1.md#23-质量引用收口与-ocr-支持集合)；其他语言仍仅有
[功能内语言预备](roadmap/architecture-post-refactor-review.md#鉴定与扫描的功能内语言预备)。

`services/scanning/pipeline.py`统一捕获、单解析线程及汇总；图片文件读取与装备状态检测归
`integrations/vision/equipment_state_detection.py`。后续规则评估共用
`services/inventory/post_action_evaluation.py`，评分公式仍归 `ScoringEngine`。
只有捕获数、成功解析数均等于请求数，且失败、补录和重复集合都为空，才进入可选状态评估。
评分返回后再次复核同一取消和账号代次，随后才按需加载既有状态操作 owner；部分成功及 issue
原样报告，不在流水线重发。该解析门禁不赋予正式库存身份，队列及原生物理等待也不据此声称有界停止。

图片成功不等于库存提交。流水线保留真实独立暂存路径，不提前替换旧截图；补录确认后
`VisionCommitController` 复用 `PageCommitLane` 接受一次冻结请求，后台 `services/inventory/vision_commit.py`
先预检文件、再经完整门禁授权背包事务、最后调用 Integration 发布。GUI 不等待该提交线程或执行这条文件移动循环。
同账号保存后取消仍展示已保存事实；旧代次和关闭 owner 不交付，物理任务终态前不销毁 owner。
退出草稿确认通过后先撤销 token/交付，再进入窗口物理等待；取消退出不永久关闭 owner。
展示失败与业务提交失败分开，延迟计算复核相同结果、账号代次、取消 token 和 owner 状态。
DB 与文件结果独立，不伪装成跨资源原子事务：已保存后的文件失败保留 snapshot_id，不重放 DB；
Service 提供严格核对当前指针/原文件身份的文件重试，GUI 通过原提交 owner 显式触发，不重放数据库。
`features/scanning/file_workflow.py` 编排原暂存继续解析与已保存后的文件发布重试；两者复用原冻结账号、
代次、资源、数量和捕获驱动，不重新截图、不重新选择目录、不重新启动游戏输入。完整暂存身份失效即终止。
补录普通关闭只恢复本次未提交草稿及原截图身份；停止、重置、退出与过期回调不恢复、不弹迟到确认。
文件重试只消费原保存事实、当前指针和原发布计划；恢复不完整或已发布但清理未完不自动重试、不重新入库。
目录租约、精确文件恢复、容量与保留规则见 [截图发布契约](integrations.md#截图暂存与发布)。
离线输入及旧 workflow 后置归档保持其独立路径，本切片不代表全链崩溃恢复。
`vision/incremental_scan.py` 独占 NEW 标记匹配与增量采集。自动模式从同一份字节冻结
`CaptureTemplateFile` 并解码，基础和缩放矩阵不可写；匹配前后、输入派发及截图写前复核标记。
半自动由用户确认截图和结束，不加载 NEW。采集拒绝遗留增量文件，截图排他创建，重试不覆盖保留原图。
`features/scanning/incremental_workflow.py` 将真实发出结果的 worker、冻结账号依赖、标记身份及
取消 Event 交接给解析；迟到 worker、代次失效或停止不启动解析，不把停止计作成功。
首图比对在 `VisionWorkerThread` 内复用本批 `BatchProcessor`，界面不构造第二 OCR；普通比对失败保留
probe，取消或 OCR/识别资源故障终止本批。删除候选与重复统计随该次结果冻结，不取下一任务临时字段。
采集标记附到同一图片 profile，提交及真正文件变更前复核；文件批次入口检查完整模型/字典，逐项变更前
仅检查取消/代次与小型标记。增量只解析整理截图，不生成正式库存；全量的最终 DAO 授权继续复核完整资源。
文件整理为逐项操作，中途停止可保留已完成变更和余下原件，不宣称整批原子或跨进程采集隔离。

原生 UID、评分、库存持久化和保留策略仍由对应业务 owner 承担，不进入识别包。
视觉测试按 `tests/vision/` 归组，词条目录规则归 `tests/domain/`。

### 2.6 鉴定输入与批量匹配

`src/services/identification/` 归属不依赖 Qt 的匹配器及冻结图片/装备批处理请求。
图片鉴定、手工鉴定的后台入口通过普通函数与请求传参，不读取 Controller、控件或当前账号。
图片选项在主线程选定后固化；卡带归一化只修改请求深副本，不改原表单对象。
匹配侧冻结用户库与当前选定静态库路径，每批生成一次图纸、共用一个匹配器；
评分消费该批图纸所用的角色权重，不再逐件读取账号权重或复用 Controller 的跨请求图纸缓存。
仓库固定快照鉴定消费同一个 `equipment_service`，其已有任务编排边界保持。

`IdentificationTaskController` 持有两阶段任务身份与取消 Event，复用 `PageTaskLane` 的单 worker/latest 合并；
普通 read 只携带冻结请求，账号/代次核对、确认弹窗之后的再核对与页面投影均在主线程。
取消使交付失效，不等于 OCR/系统调用已停止；账号重置与公开 close 先撤销交付，窗口关闭仍等待物理线程终态。
剪贴板临时文件归 Integration，按原任务账号目录及确切前缀识别；旧任务和排队 latest 均不再读取后才清理，
清空时保持启动按钮禁用到物理空闲，不因丢弃回调而提前销毁线程。
批处理在初始化、逐图片/装备及交付前检查取消 token，图纸求解复用原有取消入口。
静态/词条、图片模板/布局、模型/字典与增量扫描标记沿用前节公共资源；装配与倒带的单任务引擎
所有权也由前节执行 owner 负责，不宣称外部调用可被立即中断。
手工与图片输入行为验证归 `tests/identification/`，不为迁移建立旧模块兼容壳。

### 2.7 养成规划、历史与进程边界

`src/domain/progression/` 按三种纯责任组织：`materials.py` 统一经验换算、正式三合一材料族和库存抵扣；
`stamina.py` 统一普通掉落与材料流求解；`history.py` 同时校验历史输入、结果及角色关联。
历史格式和求解实现不再通过相互延迟导入衔接。包入口不加载数值库，SciPy 仍仅在实际求解时按需导入。

`src/services/progression/` 归组角色/弧盘/技能的材料需求、单/多角色计划、已有材料读取与历史编排。
`history_adapters` 统一保存结果投影和原样恢复种子；Domain 保有格式与校验，`history_service` 仍独占草稿绑定、
删除 write_epoch 与事务编排，DAO 持有实际 SQL。历史适配不写角色档案、不重新解释旧结果。
已审计旧发行的货币修正保留在 `legacy_currency`，只修成本/掉落，不改用户已有材料。

`src/features/toolbox/cultivation/` 集中整页、单/多角色编辑、结果、历史与控件；工具首页和窄导航仍在工具箱父包。
唯一历史列表绘制委托归其真实视图，不增设展示代理层；现有交互、对象身份和主题规则保持。
单/多角色读取共用 `CultivationCalculationController` 与现有 `PageTaskLane`：一个物理线程、一个末次请求，
身份 provider 只在 GUI 校验，后台仅执行普通 Service 与冻结请求，不捕获 Controller。准备缓存绑定账号/资料及目标，
关闭立即撤销交付但不等线程；导航退场与历史替换通过 `delete_when_page_tasks_finish` 等物理终态再销毁，
保留原父对象，使主窗口退出仍能发现任务。该销毁工具本身不撤销已接受的 `PageCommitLane` 提交。
历史 Controller 复用 `PageCommitLane` 串行持有一个物理任务；尚未开始的同类读取只留末次，保存/删除不合并或重放。
所有后台动作是普通 Service 与冻结参数的 partial，页面身份只在 GUI 核对；异常按类别返回，不记录原始恢复内容。
Controller 关闭立即撤销排队动作、Service 草稿绑定及迟到交付，不等待数据库或分离线程。
工具页通过组合根注册既有 `AccountLifecycle`，在账号身份替换前主动退场，隐藏页也关闭；切换后不自动恢复草稿。

历史事务最后一次有效性检查是提交授权点，而非 SQLite COMMIT 成功：保存核对完整信封，删除核对账号/资料及关闭状态。
身份 provider 返回后再次在状态锁内复核关闭；授权前撤销令事务回滚，授权后允许冻结旧账号事务完整提交或因数据库错误回滚。
状态锁不跨数据库 COMMIT 持有；关闭后不补偿删除、不自动重发、不将旧结果交付新页面。物理任务仍由原父对象保留到终态。
`src/integrations/progression/solver_process.py` 独占 IPC、子进程重建、序列确认及超时收尾，直接消费既有纯领域入口，
不反向创建 Application Service。默认猎人等级 policy 和求解上限与原无外部来源服务一致，不变更养成算法。
图鉴静态 importer 与通用目录查询保持各自职责；按角色更新的接入清单见仓库版本更新 skill。

倒带交互归 `src/features/toolbox/rewind/`：角色/槽位选择、方案渲染与执行窗口分别由具体模块持有，
工具箱父包只保留首页、入口与窄导航。已保存阈值校验与角色选择展示归 `role_selection.py`，
不为单一小函数保留转发文件；当前策略的槽位草稿、取消、旧结果失效及后台任务所有权保持原链路。
实际倒带输入与形状读取归 `src/features/drive_assembly/rewind/{execution,shape_detection}.py`，
装配父目录只保留装配交互；OCR 仍借用同任务 owner，输入失败和取消释放原有设备状态。
扫描动作后状态同步归 `src/services/scanning/state_sync.py`，只收敛结果类型，不改变真实输入或后验边界。
`src/services/rewind/` 和 `src/domain/rewind_loadout.py` 仍负责推荐编排与纯输入，交互归包不改变求解口径。

### 2.8 只读静态资料库服务

`src/services/static_catalog/` 按 `characters/`、`forks/`、`monsters/`、`mechanics/`、
`formulas/`、`misc/` 归组领域投影，跨领域正式术语集中在 `common/terminology.py`。
各包入口只说明职责，不聚合导入或保留旧平铺别名；Feature、养成消费者与组合根直接依赖具体模块。
怪物活动期定义和挑战投影合在 `monsters/feast_periods.py`，不让排期注解替代正式数值或往期条件证据。

`service.py` 只冻结请求、路由搜索/详情/关系页和关闭 provider；SQL、连接及发行身份验证仍由各实际 owner 持有。
一次请求同时冻结主库和显式领域路径，搜索与详情复核全部来源；图鉴与主库不因目录调整而混读。
正式 record/domain/成长身份不依赖显示名，成本别名和语言回退只由既有公共术语规则处理。
关闭逐一收尾全部 provider，一个失败不跳过后续资源；不新增查询连接、运行时注册器或统一大服务。

`tests/static_catalog/` 保护真实搜索、分页、跨来源、正式身份、养成、路由、取消与关闭；
删除纯源码拼写、颜色和尺寸装饰断言，不删除可操作性或内容完整性的行为保护。
术语门禁递归读取新功能目录，避免归包后因旧文件名通配而静默漏检。
本批验证与仍未交付范围见[首批验收](validation/architecture-renewal-day1.md)。

### 2.9 战报采集服务

`src/services/battle_report/capture/` 归集单场采集链路：`service.py` 拥有 worker 与状态，
`contracts.py` 定义客户端和持久化窄接口，`lifecycle.py` 合并启动等待与有界停止，
`polling.py` 只重试 record/axis 查询超时；`metadata.py` 与 `build_context.py` 分别核对来源信封和冻结面板证据。
目录不聚合重导出，也不保留旧平铺入口；Feature、持久化和原生面板服务直接使用具体模块。

`src/integrations/capture/raw_retention.py` 独占调用方指定目录内原始诊断抓包的文件枚举、stat 与保留清理。
战报清理事件投影仍由 `BattleCaptureService` 持有；库存同步保留原调用时点与原生来源排除，
两个 owner 直接消费同一文件 IO 入口，旧平铺抓包保留服务不再作为兼容壳。

启动 helper 不接收未使用的 writer/operation 参数；冻结账号与代次仍由注入的持久化端检查。
抓包等待只接受本次 operation 的就绪，原生等待只重试声明的暂态原因；停止超时请求 abort 不等于底层线程已退出。
两种来源的快照时点保持独立，比较元数据只写 Calc 信封，不改原始摘要或逐击。

`tests/battle_report/capture/` 保护来源、取消、最终分页与保存；保留长等待及真实 Service 成功/失败链路，
不重复验证同一短重试分支。共享夹具与消费者按当前入口维护。

### 2.10 战报冻结输入与持久化编排

`src/services/battle_report/inputs/` 中 `preparation.py` 只拥有范围证据预备缓存与重试时点；
`scopes.py` 统一首击身份核验、尝试选择、队伍成员及同队伍子集裁剪；
`settlement.py` 统一首击缺字段组的结算兜底和家具来源投影。预备缓存不是首击事实，
后续读取不覆盖 pinned 半场；子集保留原来源身份/数量/覆盖信息，不生成正式完整库存。
家具未知、冲突或非法值保留账号兜底及来源证据，明确 inactive 才产生零。

`battle_report/persistence.py` 复核冻结账号路径/代次，编排 begin、bind 和 finalize；DAO 持有事务。
原生首击和抓包结算的冻结时点保持独立，`settlement_native` 重试复用已经冻结的输入。
历史恢复、租约函数级导入、Feature 及离线恢复/评估工具直接使用具体模块，没有旧平铺兼容入口。
现有租约调用关系仅迁路径，不作为新增跨层依赖模式；本批未改变首击算法、迁移或分析引擎。

关键持久化回归与队伍子集分别归到 `tests/battle_report/persistence/`、`inputs/`。
共享纯字典 attempt helper 不依赖测试模块的生产导入；回归保护关键行为，不固化私有字段布局。

### 2.11 原生战报整页分析

`src/features/battle_report/` 根目录保留组合依赖、总 Controller 和 Page；内部按 `capture/`、`history/`、
`analysis/`、`editing/`、`marginal/`、`hits/`、`timeline/` 定位采集、历史、分析、持久编辑、可丢弃草稿、逐击和时间轴。
公共装备选择归 `editing/`，共同结果投影归 `analysis/`，不经边际页面转发。时间轴延迟聚焦绑定发起时的分析快照，
编辑器装备替换携带该详情的静态路径与 schema，过期详情或已切换页签的回调不应用。

`src/services/battle_report/analysis/native_page.py` 拥有原生整页请求、后验核验和派生目标快照的尽力保存；
`page_cache.py` 只保存有界压缩结果。旧平铺入口删除，组合根及维护工具直接依赖具体模块，不聚合重导出。
DAO 与日志依赖仍在确有派生保存时加载，目录调整不令页面首次读取额外初始化持久化。

`src/domain/battle_report/` 按业务责任组织纯模型与规则，消费者直接导入具体模块；包入口不聚合重导出。

| 子领域 | 责任与入口 |
| --- | --- |
| `capture/`、`history/` | `summary.py`／`state.py` 保存采集摘要、半场观测及瞬时状态；`records.py`／`transfer.py` 保存历史身份和可移植包规则 |
| `timeline/`、`buffs/`、`replay/` | `events.py` 区分原始逐击与派生微秒事件；两处 `models.py` 分别保存 Buff 证据／投影和技能公式／候选重放结果 |
| `build/`、`targets/` | `inputs.py` 保存角色基线及单场修改输入；`profiles.py`／`encounter.py` 保存目标档案、遭遇候选和派生身份 |
| `counterfactual/` | `quantification.py` 维护量化四态与守恒；`results.py` 保存固定轴、Buff、边际结果及冻结候选和纯属性标识 |
| `analysis/` | `composition.py`／`snapshot.py` 组合范围汇总与分析快照；`page_results.py` 拥有逐击共享详情和加载结果；`backend_contracts.py` 声明显式注入的后端能力 |

字段顺序、默认、未知值和既有浅冻结语义保持不变；候选字典的防御性复制仍由会话 Service 负责。
正式 SQLite、战报 JSON 和 wire 不存 Python 模块路径；开发工具的旧临时 pickle 随模块迁移重新生成，不增设兼容解包入口。
`src/services/battle_report/marginal/` 归组候选副本、范围准备、分母状态和显示投影；
旧计算入口仍只作离线差分，正式页面继续只经原生分析 gate，不增加 Python 页面回退。
数量/来源/未知状态和请求回调的取消检查沿用原 contract，旧平铺模块不留兼容壳。

`src/services/battle_report/build/` 收敛单场修改副本与固定轴输入责任，不另建 owner 或聚合入口。
`equipment.py` 合并装备冻结、角色页单向导入及导入锁投影；`edit_history.py` 沿用历史服务的账号 DAO 门禁，
`edit_projection.py`、`inventory_pool.py`、`profile_normalization.py` 与 `stat_reconstruction.py` 分别承担显式覆盖、
一次快照候选、来源归一和独立候选面板重建。`quantification.py` 合并逐击/生命桶与角色汇总，缺口和启发式值保持分离。
`counterfactual.py`、`offline_analysis.py` 及 `timeline_projection.py` 继续只作 DEPRECATED 离线差分；正式页面仍只经上述原生入口。
保留的关键测试归 `tests/battle_report/build/`，原平铺入口删除，不留新旧双路径。
字段顺序、必填未知值、默认值与浅冻结保持原义；目录字典等成员不因 `frozen` 自动深冻结。
`src/integrations/battle_analysis/` 归属原生战报响应：`page_wire.py`、`hit_details_wire.py` 和 `payload_wire.py`
分别解码页面、逐击共享详情与压缩片段，`editor_facts.py` 适配冻结编辑事实。页面解码在函数内导入上述 Domain 类型，
不导入离线算法补算；原生字段校验、同事件引用恢复、非空算法修订门禁及未知 HP 语义保持不变。
跨能力客户端与进程生命周期仍由 `nte_analysis_core.py` 负责，采集协议仍归 `nte_core_battle.py`，不混入此子包。
原四个平铺入口删除，Service、工具和测试的动态 patch 目标一并迁移，不保留转发壳。

`analysis/progress.py` 合并真实进度事件、回调和单请求整体阶段估计；旧两个进度入口删除，全部消费者直接迁移。
阶段次序、权重与重复分析推进保持原义，只有成功 complete 事件到达 100%，估计不是计时器。
正式 Request/native gate、离线差分和推断算法仍由各原 Service 持有，不保留旧 DTO 兼容重导出；
解码类型依赖收敛不代表 Controller 或历史读取的整个导入链已去除离线定义。

每次请求冻结账号/代次、静态/语义/组件文件身份及完整显式选择器；原生输入 digest 前后变化即取消。
缓存键包含请求、digest、文件、引擎和 dataset 身份，嵌套候选顺序保留；倾陷结果只可满足同范围概览，
不反向复用、不代替逐击。缓存命中仍复核身份、独立解码及最终交付，不重复保存派生快照。

派生快照先解码与核对，再经现有 DAO 保存；已有用户确认优先，取消继续上抛，普通保存错误只告警。
查询后检查不额外构成事务原子保证，也不修改原始战报。LRU 条数、压缩/原始大小上限保持原值。
`tests/battle_report/analysis/` 保护这条公开链路；独有资源上限、来源、取消和未知值测试保留。

`src/services/battle_report/buffs/` 持有通用 Buff 推断、逐击属性投影和移除反事实。
显式参数、输入与目标范围归 `semantics.py`；纯半开时间及作用范围索引归 `interval_index.py`，
触发、冷却、停时与区间证据编排归 `interval_support.py`，不让后者的依赖污染索引冷导入。
请求内投影 memo 与候选批只在同一冻结请求复用；消费者直达具体模块，原平铺入口删除。
正式页面仍只走独立 AnalysisCore／native 门禁，不恢复已弃用 Python 分析入口作为页面回退。
关键来源、未知、区间、计算、取消和缓存回归归 `tests/battle_report/buffs/`；未覆盖范围见首批验收。


`src/services/battle_report/damage/` 区分正式来源的伤害渠道构成、完整直伤公式批和固定轴反事实倍率；
面板/目标乘区与结构化重放比值分属具体模块，不复制已有公式。相同的有限非负倍率判断只保留一份。
`src/services/battle_report/replay/` 持有冻结轴重放、所选分支上下文、逐击审计及直伤/特殊/倾陷数值与证据渲染；
批次只属本次请求，按原轴回填并保留取消检查。纯 `support.py`、`formula_catalog.py`、`formula_cache.py` 不聚合重放 owner。
`buffs/hit_explanation.py` 只展示 Buff 采用与来源；来源、未知、计算及原生页面门禁沿用上文，不增加页面回退。
关键回归归 `tests/battle_report/damage/`、`replay/` 与 `buffs/`；同责小来源测试合并，私有重导出及纯标题断言删除。

`actions/` 持有逐击动作分组、形态归一和冻结动画候选，输出仍为版本化推断，不改逐击事实。
`native/` 归组冻结批次序列化、响应索引及字段恢复；正式页面的分析能力门禁保持独立，纯包不聚合后端。
协作攻击角色的来源与 Buff 归 `characters/linko_inference.py`、`linko_buffs.py`；
`environment/` 分别拥有条件、轨外赛季、半场确认、期数与队伍范围，两个半场和目标属性不混合。
`creation/` 分别持有受益者、反事实、估计和结果投影；参数、来源、未知和资源证据各守原有职责。
相关回归随实际领域归包，旧平铺入口和纯文案／私有结构保护删减，原生缺组件的跳过不代表实机验收。

`inputs/axis_hits.py`、`formal_tags.py` 与 `hp_overlap.py` 分别持有只读逐击拆分、正式标签与生命区间诊断；
`damage/skill_evidence.py` 消费本次冻结技能/面板和请求内 DAO 查询缓存，不把补证写回原始逐击。
覆纹状态和来源索引共用 `damage/weave_source.py` 的 sequence、目标、半场、方向四维键；
同微秒 GE 弱补证仍限定同角色、同半场且只有一个正式候选，歧义继续未知。
`mechanics/dot_state.py` 独占 DOT 正向状态时序，`dot_evidence.py` 统一只读结果和证据呈现；
`native/hit_state.py` 保留原生结果校验，不重复状态机。`native/team_state.py` 仅派发冻结计划，
灵可、达芙蒂尔及赛季的具体结果恢复由各自 `characters/`、`environment/` owner 持有，避免反向私有调用。
伤害叠层、套装和触发条件分别归 `buffs/`，固定暴击与环合比值分别归 `damage/`，不合并不同公式与未知值规则。
这些离线/协议适配模块不赋予正式页面 Python 回退；算法派生版本和组件 engine/protocol 兼容身份分别维护。


### 2.12 战报历史读取与投影

`src/services/battle_report/timeline/` 持有纯时间轴展示：`clock.py` 统一区间合并及 elapsed/active 可逆投影，
`stop_projection.py` 保留时停来源与未知状态，`projection.py` 汇总展示区间；原裁切、半开边界及平台段选择不变。
`src/services/battle_report/treatment/` 区分机制事实、原生适配和既有离线重放，复用同一时间投影及角色匹配，
不把推断值变成原始事实，也不启用 Python 页面回退。两个包的入口仅说明职责，消费者直接导入具体实现。

`src/services/battle_report/history/` 收敛历史读取责任；包入口不聚合导入，调用方显式使用具体模块：

- `access.py` 合并账号校验与历史列表读取，唯一 `StaleBattleReportContextError` 仍由 `BattleReportHistoryService` 门面公开同一类型。DAO 打开前复核冻结代次，打开后核对 profile 和代次；profile 或校验异常关闭新 DAO，不把此边界描述成每次查询后的全程校验。
- 历史列表优先展示用户确认环境；否则只读取可兼容的已存推断。兼容检查包含 schema、非空算法标识及可获得的静态 dataset 身份，不要求旧行算法等于当前生成算法；列表不同步推断或保存派生结果。
- `projection.py` 负责存储行与半开分析范围的纯投影；角色跨半场时范围保持 `None`。`evidence.py` 以正式 ID 和采集属性本地化本次证据展示副本，不回写原始逐击。

`role_editor.py` 物化 shape 字段与本场多角色详情，原始事实不回写；静态共源规则见[角色读取边界](#21-角色配置领域入口与测试发现)。

历史行为测试归入 `tests/battle_report/history/`；分析输入与编辑事实归入 `analysis/`，DAO 行为归入 `persistence/`。
历史门面仍只编排既有能力，页面分析不增加 Python 回退；共享夹具的整理不改变这些业务边界。

### 2.13 库存同步编排与最终事务复核

`src/services/inventory/sync/` 持有同步运行循环、完整候选稳定器、观察等待、计数诊断、展示状态和客户端消费协议。
包入口只说明职责；正式消费者直接导入具体模块，原八个平铺实现删除。
`InventorySyncService` 保留真实线程、单槽事件、状态通知、完整库存守卫和公开停止入口，不是兼容转发壳。
`src/services/native_session/session.py` 持有唯一共享原生连接及战报/库存租约；`performance_control.py`
消费监控线程冻结的账号代次，启用在锁内连接前与派发前复核权限、上下文和关闭状态。
未知派发保留原 `(client, key)` 性能 owner；停用仅清理原连接，不重连或要求启用权限。
`inventory.py` 合并库存租约与修订去重/退避，保存仍复核同一候选，外部写未知仍占用且不重发；
`inspection.py` 合并在途状态 single-flight 与只读检测，不借新连接接受旧结果；
`maintenance.py` 持有维护门禁，实际业务 owner 停止并释放后才断开，不改功能偏好。
捕获等待及最终写前操作复核归 `src/services/inventory/sync/capture_wait.py`；
稀疏角色投影与独立字段组筛选归 `src/services/official_role/native_profiles.py`，
共用正式静态身份和成长纯值投影，保存事务仍由原档案 owner 持有，未知字段保留原值。

完整库存只有通过来源、数量、UID/角色关系、原生修订及静默窗口后才提交；装配守卫比较本次冻结 UID 集合，
不拿历史件数猜完整性。运行循环将既有 `require_inventory_operation` 传入 DAO 的 `before_commit`，
在最终事务内复核停止、账号代次及权限；撤销回滚候选和当前指针，直接由外层 owner 收尾，不进入保存重试。
该检查不跨 SQLite COMMIT 持有跨线程锁；检查通过后到 COMMIT 之间的撤销不在本次回滚保证内。
成功提交不被之后的取消补偿回滚；原生 payload 代次不替换为应用账号代次。

独立全部物品归档与装备编排合在 `runtime.py`：写入成功后才确认 lease 中的同一候选对象，取消/失败不确认。
提交后确认失败不冒称归档回滚。抓包侧观测只关联本次会话已提交的库存，不成为正式背包或推进当前指针。
观察等待返回独立的完整证据副本，同内容可以只推进观察游标，不伪造新数据库快照。

关键验证归 `tests/inventory/sync/`，事务回归进入 core；诊断生命周期测试合并，两个纯 logger 模板断言删除。
被跨域消费者使用的三个真实夹具模块保持根目录定义，其他 held 测试不因归包改变字节。

### 2.14 加权预览的程序化保存与物理退场

桌面正式计算入口是 `ScanningController → AllocationController → NTEAppFacade`，保存走
`services/allocation/plan_save.py`。加权独立页面没有导航或生产调用者，其旧页面、窗口别名、
控件挂载、同步目录读写及空生命周期接线已删除；不以新增不可达页面替代清理。
当前差异页面所用的区段标题直接归 `allocation/results_diff_view.py`。

`features/weighted_allocation/runner.py` 保留程序化计算、冻结预览、恢复、替换和保存接口；
`WeightedSaveOwner` 仍可由显式调用方创建并注册账号生命周期，不由 MainWindow 空挂载。
owner 以账号 ID、账号代次、用户库绝对身份及不可复活 Event 拥有任务；库存 generation 与日志字段
不赋予写入权限。读取前冻结 owner，恢复绑定本次新任务，不复用历史能力。

Domain 的 `allocation/slot_binding.py` 唯一定义冻结槽位绑定与真实保存回执；不可变外壳不表示
嵌套展示字典深度不可变。`persistence_reader.py` 在一致读取中取得偏好及所选方案，默认只消费
各角色 `primary.current_plan_id`，不追历史 `is_active`；显式角色→槽位映射先复制，空映射、
缺角色、错归属、重复、归档或空当前槽整次不恢复，不改选备用槽。所选方案须同一来源快照与准确
profile 版本，保留历史逐件评分；缺失不是零，完整虚拟占位仍为零。未选备用资料不阻断所选方案，
全局真实 UID 锁照常冻结。恢复执行前后复核所选 current、来源、比较与 owner；取消或 ABA 不复活旧能力。
恢复与新计算共用一次 context 物化和静态读取 pin，但直接验证保存布局及历史评分，不先求解一个随后被覆盖的新方案。
恢复核对真实 UID 唯一、卡带数量、虚拟身份与零分、官方形状覆盖及末端锁；手动替换可偏离选优偏好，
恢复结果不宣称重新选优。只有明确发起新计算才进入组合搜索；这个程序化接口的改进不等于桌面求解器已替换。

`allocation/weighted_save.py` 只消费原冻结输入、来源、逐件分数、目标槽、比较和锁，提交前复核。
首槽 `None` 只表达创建意图，由显式槽位 DAO 与整批方案同事务创建。已绑定恢复/回执的后续替换
不重新选择目标、不读最新库存或新权重。显式槽位与主方案覆盖分别使用原 DAO writer、历史
`is_active` 策略、锁和真实 UID 冲突保护，不合并业务语义。

真实 writer 返回方案 ID 后，`allocation/weighted_cursor.py` 只读这些 ID，核对角色、实际槽位、
来源、profile、静态身份及逐件评分；首槽只由实际提交解析。确认后仅为所选槽按原冻结评分口径
重新冻结比较，生成新回执预览，不替换原 context、结果或锁快照。消费端使用回执前须再次核对
current；同槽下一次替换以刚实际提交的方案为基线，不用任意最新方案补齐。

两 DAO 在末端门闩内执行最终验证、checkpoint 和真实 commit。最终接受前撤权则回滚；接受点
已过允许旧账号事务完成，但注册该 owner 的账号生命周期仍须等待门闩释放及 worker 物理
`finished`。owner 跟踪 WorkerThread，提交复用 `PageCommitLane`；`result_ready` 不是退场。
管理 hold 期间关闭新任务，即使重建也保持门禁；超时不复活旧 token。回执读取或消费失败发生在
commit 之后时保留已提交数据库事实，不承诺回滚、不自动重试；提交后续动作错误单独交付。

库存 exporter 只有自己成功 `BEGIN` 才收尾自己的只读事务；借用外层 read/write/savepoint 时
不提交或回滚外层，错误与取消包装保持原语义。这不证明全部 exporter/prune 并发排序已闭合。
关键回归直接验证上述真实后端接口及 DAO 输入/输出，不再构造不可达页面或其私有控件。

### 2.15 分配输入、目录与失败证据

`src/domain/allocation/inputs.py` 唯一定义冻结身份、角色偏好、库存候选和 `AllocationContext`；
构造保留必填的同一 `AllocationScoringRules`、类型校验和规则 SHA，不跟随当前快照或最新 profile。
`src/services/allocation/context_builder.py` 使用调用方持有的 DAO 冻结显式版本及官方 ID 投影，
DAO 生命周期仍由调用方管理。评分规则仍经既有 `StatCatalog` 导入 `json_store` 定义，
这项遗留定义依赖不表示 DTO 构造时读取文件或 DAO，也不把本模块称为严格 stdlib-only。

`src/services/allocation/catalog.py` 保留账号／代次／路径限定的目录实例；变化 probe 只是线索，
业务 semantic 身份决定复用，stable 身份识别读取中变化并最多再读一次。无关写入不强制重建，
连续相关变化有界失败，不建立跨账号全局缓存。官方弧盘模板直接消费 `official_role/fork_templates.py`。

`src/domain/allocation/failures.py` 合并冻结输入的保守短缺诊断与一行证据文本，二者仍是不同函数。
文本不重新求解或诊断；仅全部图纸证明缺口才修正失败，零权重黑名单不伪装硬剔除，未知组员仍未知。
内核请求及词条目录仅作类型注解，文本导入不因此触发内核、评分器或 DAO 链；实际 Drive 判断保留。

`src/services/allocation/legacy_adapter.py` 只投影同一输入并调用既有 ScoringEngine／内核，
逐件确认、批量候选、冻结历史分数和取消链保持原口径。`allocation_solver.py` 与
`legacy_allocation_static_catalog.py` 保留完整真实公共 owner；不建立 shim、重导出或第二求解入口。
Context、catalog 和失败保留用例归 `tests/allocation/`；跨域真实夹具和暂挂模块保持原身份。
局部执行与整仓／实机缺口见[配装验收](validation/architecture-renewal-day1.md#8-配装领域归组与无消费者旧入口清理)。

### 2.16 账号设置、命名与模式检测

`services/accounts/settings.py` 冻结并规范化全部旧配置候选；`AccountDataDao` 在既有
`write_transaction` 内重检迁移标记和账号显式配置，只补缺失组并写入同一事务的标记。
后组、标记或提交失败均不留下部分迁移；主题和插件的本机归属不随账号迁移改变。
`services/accounts/naming.py` 编排正式账号 ID 的索引名称和用户库 profile；DAO 的改名
支持既有外层事务，不提前提交，也不通过事后无条件改回名称补偿。
账号传输由 `services/accounts/transfer.py` 编排；`integrations/accounts/transfer_archive.py` 拥有
归档成员、限额、目录安装和恢复，`storage/sqlite/accounts/transfer_database.py` 拥有一致性备份及候选用户库校验。
归档、schema/身份/完整性及正式 `AccountSettingsService.migrate/load` 全组校验通过后，才调用释放旧账号的回调。
账号身份只按正式 ID；必要旧配置精确列入白名单并经正式规范化，不递归收集任意配置。
替换恢复逐项处理；任一恢复受阻都保留原件和恢复位置，不因首个错误清掉后续回退依据。

`integrations/accounts/index_file.py` 独占同目录暂存及原子 bytes 替换，并以一个进程内
窄 `RLock` 串行化全部已知索引写者。组合顺序为索引锁 → 用户库事务；Qt、停止任务、
join 和导入 `before_replace` 回调在锁外执行。`AccountManager` 在回调后重读索引，
核对冻结的目标行和目录身份，仅合并本次目标字段。改名提交失败只在当前 bytes 仍等于
本次已发布 bytes 时恢复；补偿冲突明确保留第三方结果。这不是跨进程 CAS，也不保证
数据库与文件在掉电时组成一个强原子事务。

账号删除冻结目标 ID、索引行和目录身份；实际运行 owner 是目标时，先锁外切换到存活
账号，明确成功才修改运行态并删除。切换失败保留原 owner；切换成功后的身份冲突或
删除失败保留新的实际运行态，呈现删除待完成。导入的临时账号也只在明确离开后清理。
这些 UI 回调不取代组合根已有的停止、代次和物理退场职责，用户行为见
[功能说明](features.md)。

`services/work_modes/inspection.py` 合并纯检测判定、白名单诊断和逐域响应投影；
`services/work_modes/service.py` 继续拥有确认模式与权限策略，`services/work_modes/runtime.py` 继续拥有
探测 IO、组件操作及会话生命周期，不创建兼容转发 owner。完整成功的开发模式响应才
按正式 live capability 与本次原因投影战报／装备域；原包早门禁、失败投影、其他模式、
DTO 完整性及后验确认仍消费原有规则，能力细则见[工作模式](reference/work-modes.md)。

旧共享形状差异由 `services/characters/legacy_shape_migration.py` 编排，旧库读取 SQL
由 `SharedDataDao` 所在模块拥有，基线输出由既有静态工具 CLI 拥有。安装器的
previous／baseline 升级入口继续保留；共享历史差异不提升为官方有效面板。
账号事务／索引并发、升级迁移和模式行为分别由领域测试包维护，长期保护公开行为而非
旧平铺入口或装饰控件结构。

### 2.17 SQLite 领域实现与统一连接

`src/storage/sqlite/` 保留公开的 `UserDataDao`、`StaticGameDataDao`、构建借用读取入口
`StaticGameDataBuildDao` 和共享旧迁移 `SharedDataDao`。账号 SQL 分别归 `accounts/`、
`characters/`、`inventory/`、`equipment/`、`battle_report/`、`progression/`、`sharing/`；
只读静态查询、图鉴及静态弧盘投影归 `catalog/`，不与账号持久化混合。
各目录不聚合导出、不创建连接。`UserDataDao` 的 mixin 顺序和公开接口保持，各领域仍借用
`user_data_base.py` 的唯一连接；事务、保存点、历史备份和 `schema/` 已发布迁移保持统一所有权。

例如配装保存从 Service 进入同一 `UserDataDao`，由 `equipment/` 的方案/槽位/锁实现完成
原事务；库存来源仍由 `inventory/` 校验，业务归包不把一次写入拆成多个连接或提交。
战报逐击暂存与最终轴共用 `battle_report/value_validation.py` 中完全相同的校验；
摘要可选文本的假值规则单独保留，缺失证据不因归包变成零。
静态运行入口仍在原文件解析资源根、拥有只读连接与注册寿命，构建入口只借用构建 owner 的连接。

## 3. 应用与账号上下文

`src.app.context` 提供：

- `ApplicationPaths`：发行资源、静态库、共享库和全局配置路径；
- `AccountContext`：当前账号数据库、配置、截图和日志目录；
- `AppContext.generation`：账号切换代次；
- `AccountLifecycle`：后台能力的停止、重建和恢复。

长任务冻结账号 ID、用户库路径、generation、快照、静态 dataset、profile/配置版本、配装 `slot_id`、
锁快照、token 和输出目录。写入或回调前再次核对；过期结果不进入新账号页面和数据库。

账号切换先构造目标设置，再停止后台任务；账号与设置成对发布、generation 递增，随后重建窄服务和页面、
发布索引，最后恢复原本允许自动运行的服务。构造或停止失败不发布新身份；提交前重建、页面或索引失败，
恢复旧账号与设置并再次递增 generation，使失败目标和旧代次的回调都失效，不把代次回退。
补偿受阻时禁止创建新的账号 DAO，并由创建会话时注入的权限入口阻止新的外部操作；暂停自动服务、冻结页面，
保留停止/清理/退出出口。该保护与工作模式的 automatic 暂停语义独立。索引已发布后的启动失败只报告自动服务待恢复，
不撤回已经完成的账号切换，也不重放启动。背包同步、战报捕获、扫描、鉴定和游戏输入通过公开生命周期协调独占资源。
`GlobalHotkeyManager` 每次只冻结一个 owner 的账号级按键配置；战报 owner 仅在活动采集会话注册重录动作，
由 Qt 信号把监听线程事件投回 Controller。Controller 在双击确认后请求 Service 丢弃暂存 capture，等旧 worker
终态回调后才创建新 operation，并跨两次 capture 保留原背包同步恢复责任。

页面只读任务由 `PageTaskLane` 有界持有，一个活动读取及一个最新待办；过期结果丢弃，退出以定时回调等待线程终态，
不在 UI 线程等待 SQLite。角色及基础权重提交另由 `PageCommitLane` 持有，不合并、取消或重放已接受的写入；
提交完成后才清除草稿并继续一次待处理导航。正式账号生命周期在上下文替换前阻止未结束的页面提交。
基础权重的冻结 DTO 在 Domain，Feature 工厂从当前上下文一次创建；`BasicWeightController` 只拥有任务、缓存与投影。
`features/configuration/editing/` 归编辑表单与提交/离页规则，页面 owner 和 Controller 保留公开入口；`services/characters/basic_weight/` 分别由 `service.py` 编排事务、`catalog.py` 只读物化目录。
`BasicWeightService` 拥有目录解析和整批校验，单角色权重及页面保存共用写入计划；DAO 的 `write_transaction`
在唯一连接上管理外层提交和内层保存点。批量权重/底盘/套装/形状保存、批量重置及自建角色初始化均原子提交。
基础权重控件、草稿和展示基线由独立 `BasicWeightPage` 持有；主窗口只注入上下文、导航、变更通知并消费 task owner。
页面模型以正式角色 ID 为 key，显示名只用于标签和搜索；同名官方/自建角色独立编辑。
旧 `ConfigurationControllerMixin`、JSON 编辑参数及窗口编辑转发已删除；导航 key、界面布局和操作含义保持。
账号切换先保护已接受写入，再停止读取并立即清空旧账号投影；晚到读取不能回填新页面。
隔离流程与回退证据见[首批架构验收](validation/architecture-renewal-day1.md)，不据此宣称全项目或真实窗口验收完成。
工作台摘要和计算目录分别由独立 Controller 持有；计算目录的变化探针、窄业务指纹及目录读取属于 Service，
账号依赖字段在 DAO 一致性读取边界内取值，跨连接目录组装以读取前后依赖身份复核并最多重试一次。
详细交付行为见[页面响应性](features.md#页面响应性与后台任务)。

## 4. 数据域

| 数据域 | 路径 | 当前 schema | 所有权 |
| --- | --- | ---: | --- |
| 发行静态 | `data/game_static.sqlite3` | manifest | 官方目录、成长、好感度、觉醒、技能、伤害、效果、敌人、Boss 支援模板、活动副本、高危委托、轨外轮换、争锋赏宴、魔女赐福、离线推荐权重基线和毕业模板；运行时只读 |
| 独立资料目录 | `data/role_catalog/` | manifest | `role_page` 提供角色和弧盘，`reference` 扩展只读图鉴；按领域冻结数据库和图片，不替换战报数据 |
| 公共共享 | `data/app_shared.sqlite3` | DAO 常量 | 旧版官方额外形状覆盖与迁移记录；不参与当前有效值 |
| 应用全局 | `config/global_ui_preferences.json` | JSON | 跨账号主题 |
| 账号私有 | `accounts/<account_id>/user_data.sqlite3` | DAO 常量 | 快照、角色好感度/觉醒选择、权重、自建角色、偏好、配装槽位、锁、任务、逐击、战报配装、角色属性、单一修改副本、导入来源/装备锁与用户确认目标/环境条件 |

读取优先级为账号显式配置、允许共享的公共数据、发行默认。静态库已收录的官方角色额外形状只读发行静态库，
旧公共覆盖即使存在也不参与有效值；自建角色额外形状和基础权重、
计算偏好、倒带偏好、方案、锁和任务只属于当前账号。schema 迁移只追加。

`ApplicationPaths` 保持 `static_database_path` 为完整游戏库，并通过 Integration 一次校验可选角色包的
用途、数据库身份、文件哈希和图片清单，冻结 `RoleCatalogRelease`。角色页与配装分配各自整次使用完整
游戏库的可计算模型；独立目录提供已校验的展示图片、养成工具和只读图鉴，不将缺少弧盘常驻属性及
毕业模板的 `reference` 数据集用于角色直伤或 Rust 分配。战报的静态库选择独立保持完整库。
目录成套晋升与回滚由 Integration/构建工具负责，UI 不探测路径或逐字段合并跨版本数据。

## 5. 快照与来源能力

库存表结构统一，来源能力不统一：

- `nte_core`：正式装备 UID、角色实例、可靠装备状态、仓库 RPC 和极速装配；
- `vision`/旧 `gamepad`：分析用临时 UID，可用于仓库、计算、历史展示、倒带和游戏界面自动装配；
- 运行时状态增量：只覆盖原生完整快照中已知 UID 的锁定、弃置和装备状态，不增加物品或推进当前指针。

来源权限通过公开 capability helper 判断，不从 UID 非空、表结构或 UI 名称推断。快照清理保护公开读取入口选出的当前快照
（包括无当前标记时的兼容回退）、所有已保存方案及装配任务的来源，以及本进程成功导出的计算输入；不只保留活动槽位。
导出与清理共用进程锁；导出先以独立只读连接核对来源仍在最新已提交状态中，再读取冻结视图并登记保护。
旧 WAL 视图仍看得到、但已被正式清理的来源会被拒绝，不登记假保护、不换成最新库存，也不结束调用方读事务。
清理在一个写事务内按参数预算分批删除，任一批失败整体回滚；本机制不声称提供跨进程租约。
战报结束后读取当时最新完整原生快照，只把实际出场角色的
游戏当前装备复制为独立战报副本，完成态不继续依赖库存快照。
战报重录不进入完成态物化：Service 停止当前 Core capture 后复用持久化端的暂存丢弃事务，不读取最终轴、
不生成历史记录；Controller 收到丢弃成功终态后才启动下一场，因此新旧 operation、cursor 和暂存行不混用。
战报完成后的既有后台整场分析负责将自动目标推断写入版本化派生快照；基础候选和后续残差裁决原位覆盖，
用户确认条件仍保存在独立表并始终优先。历史列表只投影已保存结果，不启动候选扫描；旧战报在单场分析完成后
惰性补齐。派生快照记录算法与静态 dataset 身份，删除战报时随外键级联删除，绝不改写 Core 逐击字段。
战报包由账号绑定的 Transfer Service 从历史 Service 与窄 DAO 读取，UI 不拼 SQL。导出冻结账号 ID、
generation、用户库路径、静态 dataset 和战报 ID 集合，经应用专用 `.ntebr` 压缩容器原子写出；读取在相同
上下文复核下校验并解压，随后以单个事务重映射战报/逐击轴本地 ID。来源库存快照 ID 不跨账号绑定，活动
配装指针不属于战报事实，均不导入。导入来源和每角色装备摘要分别写入 `battle_report_import_origin` 与
`battle_character_import_equipment_lock`；DAO 在所有写入入口复核装备摘要，不能只靠 UI 禁用。格式字段见
[战报包格式](reference/battle-report-package.md)。

战报原始角色配置与装备同样不可变。本机采集战报的外层编辑器通过 `battle_build_edit` 只维护一个可覆盖的
本场人工校准副本及其启用指针。尚无副本时，编辑 Service 必须从本场原始冻结角色配置和装备复制草稿；已有
副本时读取该副本。当前账号角色页不是编辑器的隐式初值，只能经显式“从角色页面同步”动作覆盖副本，任何
路径都不得因此改写原始快照。副本可选择本场原始、游戏当前或任一已保存槽位的空幕/驱动上下文，保存时把
当前计算装备、词条和正式 UID 完整复制进 `raw_profile_json`，不保留会随背包或槽位变化的指针；角色页上下文
沿用其满级计算口径，本场原始上下文沿用战报实测冻结值；理论最优和虚拟占位不能冻结。分析层在启用时以修改
养成与该配装覆盖重新计算面板、Buff 和逐击；没有配装覆盖的旧副本继续组合本场原始装备。恢复原始只关闭
分析指针，不删除已保存副本；导出时若存在已保存配装覆盖，始终导出该校准装备，并独立保留 `is_active`，
避免“恢复原始”丢失已经完成的本场装备校准。

角色页双向同步是独立的显式 Service 动作，写入角色页永远只传递养成字段，不写战报装备。角色页导入默认
只覆盖养成、保留既有配装覆盖并启用副本；用户显式选择“含空幕驱动”时，Service 把角色页游戏当前装备
冻结为本场独立覆盖，仍不反向修改角色页，也不保留库存或槽位活动指针。导入战报只允许不含装备的角色页
养成同步和养成/弧盘修改；其冻结装备由导入锁保护，外层与边际页都不得切换来源、替换装备或创建新的装备
覆盖。

边际子页面只拥有控件值、当前角色选择和内存中的 `BattleMarginalCandidate`。不可变原始分析始终作为唯一
重放起点：有启用的外层修改副本时一次重放到该最终有效配置，存在内存草稿时一次重放到最终草稿；不得串联
“副本倍率 × 草稿倍率”或把任一倍率乘回原战逐击。逐击锚点、角色分母和团队分母均来自同一当前最终候选，
并保留其 `partial/unavailable` 状态。临时养成、弧盘、面板和本机战报的临时装备替换只参与本次重算，
离开页面、切换战报或重新打开即丢弃，不调用 `save_battle_build_edit`，也不进入导出。角色选择同时确定其
所属半场，所有候选逐击只在该半场重放。

Qt 无关的公共逐击反事实 Service 是属性单位边际、配置试算和 Buff 移除的唯一比值 contract。它对每一击按
`(scope_half, target_id)` 从本次分析冻结的实例映射读取公式画像；具体怪物身份未知但候选画像等价时，仍可
使用共同画像完整计算，不得为了取得名称而改用 `default_monster_id` 或主要目标。画像本身未知时，只要求
候选相对基线实际改变的乘区具备输入；前后不变的未知防御、抗性或其他共同乘区可在候选/基线比值中相消。
目标敏感的同技能、同类型或其他 peer 比值必须包含同一 `(scope_half, target_id)` 和画像语义，不能跨目标或
跨半场传播；目标无关的比值只能在公式家族和因果依赖均相同的逐击间复用。

逐击反事实结果使用 `complete`、`partial`、`unavailable`、`not_applicable` 四种量化状态。`partial` 只给出
已量化变化分量，不代表完整收益或天然下限；`unavailable` 没有候选比值；`not_applicable` 才表示已经证明
本次变化与该击无关。未知不得用 `ratio=1`、候选等于原值或 `0%` 冒充零收益。为了保持固定轴完整，调整后
时间轴可以把未量化逐击以原始事实占位，但 Domain 必须把该值标成未投影，不能纳入完整候选收益。量化率只
描述原始伤害中具有可用投影的覆盖范围，变化完整性描述本次所有变化依赖是否都被计算，二者分别聚合和展示。
`BattleBuildCounterfactualService`、`BattleMarginalCalculationService` 与 `BattleBuffCounterfactualService`
只负责各自候选和聚合，不复制不同的未知回退。调整后时间轴由独立投影 Service 复制候选逐击、技能组和动作
汇总值后交给现有 Canvas；生命上限派生条保留来源角色，已建模机制按来源逐击或角色固有生命联动，证据不足
时保持未量化。Canvas 只要绘制了逐击点就必须发出可解析的选择；Page 在候选对比尚不存在时打开原始公式，
存在对比时附加候选公式，部分或未量化占位仍打开并展示缺口，不能因候选行缺失而静默返回。逐击级方法、
依赖缺口和误差属于 Domain 审计信息，Page 只按领域状态投影收益与构成。
敌方实例或场景缺少正式字段时，分析层不按展示名绑定静态怪物。Qt 无关目录 Service 从静态库装配大世界
图鉴对象、材料/养成副本类目与难度刷怪模板、高危委托逐难度怪物池、轨外之境配置/层/上下半场、争锋赏宴对象/难度/加成和魔女
赐福。大世界允许多选，并为当前计算对象选择正式世界等级/属性档；缺少正式目标 ID 的逐击必须由用户指定
一个当前计算对象。轨外之境的当前与下一
配置来自限时任务中 `AbyssID` 对应的大陆服生效区间，不能把配置 ID 末尾数字当关卡层数或按数字倒序。
副本模板按正式 `clone_level` 档闭合到属性包时，目录直接提供对应等级、防御、倾陷参数以及普通、暗、光、
咒、相、灵、魂、心灵八类抗性；少数无法闭合的模板只提供对象身份和推荐等级，防御/抗性仍需用户确认，
不用名称或数字相似性冒充精确绑定。
`battle_target_condition` 为每场记录保存一份可覆盖的账号私有声明及所选对象集合。DAO 独占结构校验与事务；
争锋抗性加成在 UI 投影为战前最终参数，魔女赐福作为用户确认的整场外部 Buff 进入同一 Buff 属性投影。
页面未保存的人工编辑不进入计算，声明也不修改逐击、目标生命样本或 nte-core 原始字段；下述反向匹配
属于独立高置信派生条件，可直接参与当前内存分析，用户保存条件后即停止自动推断。
当整场所有对敌逐击都缺少正式目标实例 ID，且声明只选择了一个主计算对象时，分析 Service 可在不可变投影
中统一绑定该对象，并保留 `user_confirmed_single_target` 证据类型；只要出现正式实例 ID、多选对象或主对象
不唯一，就不做该补齐。原始逐击与数据库保持不变。
未保存用户条件时，独立反向识别 Service 以当前分析范围内每个目标实例观测到的最高 `target_max_hp` 作为
初始上限，按目标数量及最大生命多重集与允许的静态遭遇配置做完整相等匹配；轨外上下半复用的 wire handle
按半场分域。Core 提供的玩法、层数、半场和怪物 ID 只作为额外过滤证据，不是启动推断的前提。单目标范围
可以把受击逐击的 GameplayEffect 索引与 ID 交叉核对到静态正式类路径，并且所有可用类路径归属同一怪物
时，将该归属作为身份补证；多目标、索引/ID 冲突或多个怪物归属时不补证，也不从 GE 展示名猜身份。通常只有一个
完整配置符合时，才在内存中投影环境、可唯一分配的目标名称/身份和共享敌方参数；多个严格候选同时符合时
保留稳定的展示默认和全部 alternatives。只有候选公式画像等价或正式身份唯一时才投影敌方参数；同生命值
候选画像不等价时，具体防御、抗性和目标身份保持未知。黑之书与无首铁驭同世界等级生命相同是产品级
例外：缺怪物 ID 时默认黑之书，保留无首铁驭候选、降为中置信并在当前范围标红歧义。投影不改写已保存逐击
或 nte-core 字段。
轨外期数先以冻结的战报 `captured_at_utc` 换算大陆服时间，再与正式轮换生效区间唯一匹配；该期数约束先于
HP/身份候选排序，不能使用应用当前时间改写历史战报。若时间已唯一确定期数且至少一个半场严格匹配，另一半
因目标 wire/生命多重集冲突时仍保留已确定的赛季，只投影已匹配半场画像并明确记录未匹配半场，不能把目标
映射冲突冒充为轨外期数冲突。时间缺失、格式无效或未落入唯一正式区间时才保留原稳定目录顺序回退。
反向候选严格限于当前/下一轨外配置、争锋全部八个挑战对象、具有逐难度正式怪物池的高危委托、经验及甲硬币/异能升级材料/弧盘突破材料/
空幕四类材料养成副本，以及异象追猎单 Boss 的世界等级变体；仅有通用 Key=0 怪物池的旧高危委托、异象巡礼、普通大世界怪群和其他活动不进入
自动匹配。争锋选项由 Rust 在同一次环境解析中消费首击冻结的官方 ID，匹配本场挑战对象的静态关系；完整空列表与缺失证据分开处理。
缺少该列表时，完整目标 Buff 快照可解析有正式绑定的选项，其余保留既有实测生命推断及选项预选。
具体优先级见[战报功能](features.md)，所有结果进入同一个可编辑条件选择器，用户保存前仍可修改。

`src/services/battle_report/targets/` 持有正式目标目录、半场实例映射、生命账本与版本化推断快照；
`encounter/` 持有冻结遭遇候选的构建、匹配和保留的离线差分拟合。观测数值/顺序纯帮助统一在
`observation.py`，单调用的世界 Boss 候选构造并入 `encounter/catalog.py`；包入口不聚合导入或转发旧模块。
关键身份、来源、未知、范围、取消及逐击不变回归归 `tests/battle_report/targets/`，正式页面沿用第 2.11 节独立门禁。

战报中的最大生命下降不写回逐击或聚合摘要。Qt 无关的目标生命分析 Service 读取固定逐击目标样本与有效角色
养成，生成带算法版本、证据引用、机制归因置信度和数值结算置信度的派生事件；页面时段、角色贡献、伤害
构成和时间轴只消费该不可变投影。完整原始轴精确命中角色 1004 的
`GE_Player_Lacrimosa_Blood_Damage_LV6` 时，推断 Service 生成高置信 `BattleInferredCharacterFact`，表达
`Effect5` 在本场已实际生效；它不修改冻结快照、不勾选角色页觉醒，也不持久化为第二套事实。分析默认启用
该事实，并允许当前内存候选通过 `disabled_inferred_fact_ids` 停用；Buff、技能、逐击和目标生命重放共同消费
同一个有效养成覆盖。`GE_Player_Daffodill_ExtraUnbalance_Damage` 不是达芙蒂尔五觉生效事实，零觉战报保持
零觉；它同时证明零觉洞察本身已有一次额外结算。只有边际候选明确勾选五觉后，才按原轴洞察每层再生成
一次候选派生结算，因此一层总计两次，三觉叠至二层时总计三次。
目标生命归因仍只注册安魂曲五觉与法帝娅被动两个窄机制适配器；法帝娅适配器还以正式黯星结算逐击推进最多
五层的全队生命汲取，生成可删除重算的 `HPMaxUp` 区间。未知机制保留为
未归因，未来 nte-core Buff 实测按字段提高归因证据等级而不覆盖原始目标样本。
目标生命 Service 把 nte-core v4 完整最终轴中的正数结构化 `max_hp_reduction` 视为权威；目标最大生命变化
只用于一致性校验，不得再次生成一份相同结算。结构化零值默认阻止旧式样本计伤；法帝娅黯星因当前 Core
没有观测差值语义，仅在同半场、同正式目标、被动已解锁且五秒内存在 `Buff_Reaction_4_new` 时走机制定向
样本回退。其他零值下降只更新单调基线，不生成正式事件，避免后续累计误归因。字段缺失、旧 contract 或
不完整轴仍走旧式样本回退：在同一分桶内从最大生命变化前的附近连续旧上限样本取最小
可靠 `HPAfter`，按原生命比例生成上限结算；下降后首行的正式 `HPBefore - HPAfter` 不重复扣除该结算。nte-core 行先以权威
`overkill_damage` 得到有效主伤害，旧式 HP 区间重叠诊断不再作用于这些行；服务器结算残余保留为未归因，
不生成角色快照。目标账本 Service 使用“半场 + 正式目标实例”作为唯一分桶，分别汇总有效逐击、生命上限结算、首个当前生命、
最低终止生命和观测耗血。并发 `HPBefore → HPAfter` 相交只产生诊断，不修改伤害；账本通过有符号未解释差额
闭合“观测耗血 = 分析有效伤害 + 未解释差额”，差额不进入角色、技能、伤害构成或反事实。聚合摘要始终保留
nte-core 原始总伤害，目标名称优先使用遭遇配置中的本地化名称，推断和账本均不写回逐击事实。
Qt 无关的 Buff 推断 Service 从冻结角色养成与装备选择解析当前效果定义；装备套装由核心 ID 和驱动形状
联合判定 2/4 件条件。独立 `BattleEquipmentSuitService` 冻结 24 条二/四件套目录：二件套读取常驻
`modify_pack`，四件套将动作、普通攻击逐击、分属性命中、环合状态和独立层数投影为专用规则；已接管套装不再走 generic
Buff links，避免条件项双算。角色技能只采用正式 GA 事件直接绑定和
常驻 `passive_buff`，不把 Blueprint 引用可达性等同于运行时施加。Service 只读静态规范化 Buff/GE 图，
曲线系数必须经曲线行或当前精炼参数解析，再将明确触发与已有动作/逐击证据对齐为可删除重算的
`BattleInferredBuffInterval`。区间分别保存状态和数值
置信度、来源定义、作用对象、触发事件、动作/逐击引用及未解释 Calculation；UI 只消费投影，DAO 不保存
推断结果。轨外之境角色 Buff 按逐击半场边界和出场角色求交，上下半互不继承；外部环境 Buff 不参与该清空。

`src/services/battle_report/characters/` 持有冻结角色的被动、技能 Buff、觉醒门禁及专用状态。
`awakening.py` 合并已审计觉醒目录和逐击门禁，显式觉醒选择判定只保留一个公共 owner；
`shinku.py`、`zankou.py` 各归组本角色机制，但原有状态、作用范围、置信度及证据仍独立运输。
达芙蒂尔的原轴机制与候选派生结算分别归 `daffodill.py`、`daffodill_marginal.py`，不把重放公式依赖混入前者；
共享原生状态派发、取消 checkpoint 和返回值校验归 `state_compute.py`。消费者直达具体模块，原平铺入口删除，
关键来源、未知、公式和状态回归归 `tests/battle_report/characters/`，不改变前述正式页面门禁。

角色培养被动不混入静态 Buff 图的自动可达性扫描。独立 `BattleCharacterPassiveService` 以逻辑角色、突破
阶段和稳定被动 ID 冻结 44 条人工审计规则：可安全量化的属性与技能限定效果生成同一 `BattleStaticBuffRule`
contract，倍率替换进入技能证据层，最大生命和其他状态机进入窄专用 Service。角色被动目录同时保存尚需
目标、资源、生命周期或新增逐击证据的适配器身份，使固定轴兜底与后续完整重放共用一个规则来源。
正式角色技能中依赖施法者基础属性的团队固定攻击由 `BattleCharacterSkillBuffService` 单独解析：规则加载时
冻结人物与弧盘的 `AtkBase`、正式技能等级和曲线，生成普通 `BattleStaticBuffRule`；装备攻击、百分比攻击和
其他运行时 Buff 不进入该来源基数。动画内延迟生成的效果由显式动作偏移确定起点，避免前置逐击倒吃 Buff。
`src/services/battle_report/forks/` 将弧盘定义、精炼、固定轴状态和来源证据按具体 owner 归组。
参数读取的相同实现只在纯 `parameters.py` 保留一份；重复时钟函数归 `clock.py`，两族原签名与停时边界保持，
不把重放或原生后端依赖引入参数／默认策略冷导入。`default_policy.py` 继续单独保存产品确认的假定及版本，
不覆盖强来源证据。消费者、动态 patch 和测试夹具直达正式模块，原平铺入口删除；
关键默认策略、未知、来源、取消和时序回归归 `tests/battle_report/forks/`，原公式、模型版本及正式页面门禁保持。

已人工审计弧盘同样通过独立 `BattleForkRefinementService` 接管，49 组精炼效果各有且仅有一个所有者：
22 组既有专用状态机、16 组伤害优先补全状态机和 11 组生存/资源/运行时条件适配器。通用推断入口只负责
选择所有者和合并不可变区间；`team_others` 作用域只覆盖来源角色以外的队员，`character:<id>` 只覆盖动作
分段确认的具体操控角色，二者都不退化成全队 Buff。预备备在 E 动作实际结束时取得左虎符、正式 Q begin
取得右虎符，猫协同伤害碎片不视为 Q；凑齐双符时从第二枚令牌到达时直接建立司令虎符区间，不再消费伤害
阶跃或绑定阶跃目标。Boss 条件只由正式怪物目录消费，缺少正式身份时不数值投影且不得把等比重放显示为
完整零收益。行进于时间之外把消费荒时的单次 Q 暴伤与满三层后的七十秒全伤害无视防御保存为两个独立区间。
独立属性投影 Service 只接受动态、直接数值、加法、作用对象明确且已映射乘区的修正；自身/全队
属性进入逐击角色面板，敌方分属性减抗进入同一击的敌方抗性区。冻结常驻来源、层数不明和未解析
Calculation 保留排除原因；已确认与当前精炼参数一一对应的窄 Calculation 适配器保留原路径并提供中置信
数值。边际 Service 对冻结与编辑面板同时投影同一逐击 Buff，并复用该击 `(scope_half, target_id)` 的冻结
画像；只比较候选/基线实际改变的乘区，不改写真实伤害。身份未知不等于画像未知，画像未知也不阻止未变化
共同乘区相消；只有发生变化且缺少输入的依赖进入结构化量化缺口。未来实测 Buff 流进入 Integration 的独立
事实 contract，再由融合 Service 做字段级覆盖。

## 6. 配装槽位、方案与锁

每个角色最多三个可见配装槽位，使用稳定 `slot_id`；`primary` 是旧数据默认槽位，显示名可修改。槽位
指向一个当前方案，旧方案作为历史版本保留。方案冻结来源快照、assignment、逐件评分、卡带满级值和
来源类型。

计算保留锁属于当前账号的具体槽位方案，不等于游戏装备锁。锁定真实 UID 在候选构造前联合排除，并在
DAO 保存事务再次检查。批量装配显式选择每个角色的槽位，要求角色唯一、槽位有效、原生来源和跨角色
UID 无冲突。

## 7. 副作用确认

RPC 接受、输入动作结束、按钮状态变化和任务下发都不是最终成功。仓库状态写回和装配必须保留操作前
基线，并等待操作后的递增稳定快照或范围化正式事件，核对目标 UID、角色实例、位置与状态。

视觉扫描后的鼠标状态管理是特殊会话能力：只使用本次扫描索引，逐件复核操作前后图标，不赋予临时 UID
后续写回能力。超时进入待确认或失败报告，不伪装为成功。

## 8. 导航与生命周期

一级导航为工作台、计算、配装、角色、仓库、鉴定、战报、工具和设置。角色图纸、基础权重是角色子页，
通过 `parent_key` 保持父导航高亮。账号切换、页面销毁和应用退出都走公开停止入口，使旧 token 和旧
generation 结果失效。

战报 Controller 的 `final_close` 负责独立 HUD 的一次延迟删除；普通停止与账号重置保留该控件，
不把无原生父窗口误作无生命周期 owner。边际面板在编辑器销毁前停放到隐藏页面父级，重新附着时显式显示。
非侧栏导航按钮及隐藏战报控件也有明确 QObject 父级，归属接线不使隐藏控件意外可见。
这些责任与任务 token／worker 物理退场分开，局部控件寿命证据不代替完整应用资源稳定性验收。
