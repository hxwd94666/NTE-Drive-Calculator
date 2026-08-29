# 外部集成与扩展

本文说明外部能力进入项目时的边界。业务所有权与生命周期见 [系统架构](architecture.md)，真实环境步骤
见 [Windows 验收](validation/windows.md)。

## 1. nte-core

`src/integrations/nte_core.py` 负责 stdio 协议、进程、事件和错误适配，不负责评分、配装或 UI。当前产品
使用的能力包括：

- `capture.detect`；
- `capture.start(profile="inventory"|"combat")` 与 `capture.stop`；
- `inventory.get_latest`；
- 仓库状态写回与极速装配 RPC；
- `event.battle.summary`、`battle.get_summary`、`battle.get_record`、`battle.get_axis`、`battle.reset`。

`inventory.get_latest` 读取最近捕获结果，不等于强制刷新。完整库存事件进入快照稳定化；残缺状态事件只
更新固定完整快照中的已知 UID。RPC 接受只代表已提交，最终状态由后续稳定快照或正式范围事件确认。

设置页的 nte-core 抓包诊断只调用 `capture.detect`，并以 Windows 只读探测补充 Npcap 驱动服务、已启用
适配器、Npcap 安装痕迹和常见 `wpcap.dll`/`Packet.dll` 位置的摘要。它不创建抓包会话，不读写网络配置，
不输出 MAC、IP、远端地址、完整本机路径或核心 capability 清单。`capture.detect` 返回零设备时，报告按
安装缺失、驱动服务、无活动网卡或“驱动/过滤器/运行时待排查”给出下一步；核心没有提供 libpcap 枚举错误
文本或逐网卡过滤理由时，报告必须明确该边界，不能伪造原因。

新采集要求 nte-core battle contract v4；旧 v3 战报只保证可查看，不补写新字段，也不承诺按 v4 重新校准。
v4 继续保留逐击 `overkill_damage`，并新增结构化 `max_hp_reduction`。正数表示 Core 已对该来源逐击完成生命
上限扣减校准；零值只表示 Core 没有为该击生成结构化修正，不证明所有尚未注册语义的机制都没有发生。
应用不得恢复全局样本猜测，只能按[战报功能契约](features.md#9-战报)列出的已审计机制做窄回退。record ID 在当前 Core 进程内稳定，
generation、cursor、sequence 和总数按十进制字符串保留；axis 每页限制 1～500 行。逐击页携带 generation，
应用在停止采集后从空 cursor 重读，并逐页核对 generation、record ID、`finalized` 与 `complete`；Core 不额外
提供逐击来源或校准状态推断标签。

`overkill_damage` 只对应 primary `damage`，不包含追击；应用校验它不大于主伤害。Core 的
`Server settlement residual` 行以未知角色返回；应用把
非正角色 ID 或 `character_known=false` 统一投影为未归因，保留其有效伤害但不生成角色养成快照。
应用在采集期间轮询并暂存逐击，以免 Core 的 50,000 击保留窗口裁剪前缀；这些页不是最终事实。停止采集后
固定最终 record generation `G`，从空 cursor 重读完整轴，要求每页均为 `G`，在账号库事务中替换本场暂存轴，
再复核 record 仍为 `G` 后完成战报。`capture.stop` 返回后 Core 已排空并冻结记录，因此任一步发生代次变化、
cursor 过期或完整性不满足时直接把本场标记为不完整，不对同一冻结状态做无意义重试；仍不
稳定时保存为 incomplete 并记录安全原因，绝不混合代次。逐击只保存
Core 已脱敏字段，战报数据库不保存网络包、端点或 PCAP。设置页显式启用账号级原始抓包后，库存与战报
启动时会冻结该设置，并由 Core 把 `.pcapng` 独立写入当前账号日志目录；这些文件不属于业务数据，不随战报
历史读取、导出或数据库迁移。

`battle.get_summary.max_hp_reduction` 是与 `total_damage` 分离的原始削减量聚合值，只随原始 summary JSON
保留供逐击合计审计，不新增页面业务指标、不单独投影为历史字段，也不进入伤害、DPS、角色贡献或生命上限
结算公式。正式生命上限结算仍只消费最终完整轴的逐击 `max_hp_reduction`，并按结算前生命比例计算有效损失。
每场采集在握手后冻结 `core_version`、协商协议、数据版本和 nte-core EXE SHA-256，并随战报持久化；同为
battle contract v4 但 Core 构建不同的记录不得仅凭 contract version 视为同一解析语义。原始 summary、
record 和逐击 JSON 仍保持 Core 返回值，不注入应用来源字段。

`team_snapshot_id` 当前为空，逐击也没有暴击事实、Buff/Debuff 区间、护盾/治疗或角色施法事件。应用不得
用当前 UI 队伍、固定暴击率或相邻血量补造这些事实。完整队伍、正式敌人实例、场景 ID、时间线 UI 和
实测 Buff 轴仍未成为 Core 能力。

## 2. 视觉、OCR 与游戏输入

`src/integrations/vision` 独占窗口坐标、截图、格位检测、鼠标动作和扫描后状态同步；`src/scanner` 与解析
Service 负责 OCR、归一化和装备字段。Integration 返回截图、索引、解析结果或诊断，不写业务快照，
不决定评分和保留规则。

鼠标与虚拟手柄全量扫描最终都提交为统一 `vision` 快照。游戏界面自动装配和实验云模式使用同一公开
输入 contract；页面不创建鼠标后端或虚拟手柄。云模式当前固定关闭，只保留映射与诊断代码。

新增输入后端必须定义：窗口身份、物理像素坐标、前置页面、动作序列、可见后置状态、取消检查、输入
释放、超时和回滚。缺少后置确认时停止当前任务，不盲目补点。

## 3. 二进制与插件

根目录 `nte-core.exe`、`dwmapi.dll`、`nte-mod-loader.exe` 和插件副本是本机文件。`third_party` 只保存明确晋升的发行组件；
晋升前记录上游 commit、版本、许可和 SHA-256，并完成协议、打包和真实 Windows 验证。

mods 插件运行时脚本与 SDK 缓存位于应用配置目录的可写 `mods-plugin` 工作区，不进入 Git 或发行模板。
游戏更新后的 presence、IPC 管道、
动态 SDK 和 Hook 排查见 [装配插件版本适配](reference/mods-plugin-version-adaptation.md)。

Mods 插件默认通过游戏目录中的代理 `dwmapi.dll` 加载。只有代理未生效时，用户才可显式选择备用
`nte-mod-loader.exe`；两种方式互斥。Loader 由 Integration 使用 UAC、唯一 stop event 和 owner PID 管理，
应用退出或用户停止时协作退出，不以强制杀进程作为正常路径。Integration 从已选择的 `HTGame.exe` 安装根
定位官方启动器，并通过 Loader 的 launcher override 明确传入，不依赖本地化卸载名称或固定盘符。Loader
启动前处理游戏目录代理 DLL，停止超时后禁止部署代理方式；Loader 进程存活只表示正在监控，装备 IPC
管道出现后才确认游戏插件已加载。运行时不固定 Loader 的 SHA-256；环境变量指定文件、随包文件或应用
目录同名文件只要存在即可启动，便于用户用可信来源的新版或修正版直接替换。

## 4. 静态数据与资源

官方数据只通过 `tools/game_data` 生成候选静态库。静态 schema v29 保存培养指南、推荐弧盘/属性/阶段、
技能说明、GameplayEffect 索引、怪物手册别名、装备 Modify/曲线，以及精确范围内的角色输入、技能效果引用、
关键效果属性和动画时间证据；同时保存敌方生命三段值、RogueLike 怪物/属性修正，并基于已导入 Blueprint
证据规范化 Buff/GE 的持续、周期、叠层、属性修正、事件触发及装备/弧盘/觉醒绑定；同时保存官方怪物图鉴、
材料/养成副本类目、难度、波次刷怪模板、高危委托及其逐难度怪物池、官方 Boss 支援模板成员、争锋赏宴和魔女赐福目录，以及限时奖励任务提供的轨外配置大陆服
生效区间，以及当前/下一配置的赛季名称、Buff 说明、正式 GE、数值曲线、已审计触发组成和怪物池官方本地化名称。当前/下一轨外配置按构建日后的正式时间区间选取，不按 `AbyssID` 数字大小推断。导入器保留来源文件、
行键与摘要。版本更新先在 `build/` 完成候选构建、工坊权重同步和毕业模板重算，再由
`tools/game_data/promote_static_release.py` 显式读取仓库外本机配置，核对配置/数据库/manifest dataset、
当前 schema/importer、全部来源 SHA-256、payload 省略、外键、SQLite 完整性及最终报告后带回滚地晋升。
禁止直接构建到 `data/`、手工复制候选或只修改 manifest；晋升失败仍视为未完成。静态库和
`data/manifest.json` 作为同一次晋升产生的原子变更审查，改变规范化输出的 importer 修改必须递增版本。

正式静态库重建前先原子备份上一发行库。存在工坊 API Key 时同步 API；没有 Key 时只从该备份继承带
`workshop_api`/`workshop_cache` 来源的权重，旧库不存在的新角色才保留本次构建回退。两条路径都重新
生成毕业模板；没有 Key 和备份时阻止发布。角色 1072 尚未实装、工坊 API 缺行时，使用
`tools/game_data/character_weight_overrides.json` 中的临时发行回退，不被通用默认值覆盖；工坊 API 一旦
返回 1072，则以工坊权重替换该回退。角色额外形状不继承旧库，直接由官方角色、底盘槽位和槽位属性修正
三表关联导入。

游戏 UI 图片由资源构建工具生成到 `assets/game_ui`，`manifest.json` 明确记录 ID 映射、文件哈希和
`unresolved_assets`。缺失资源保持显式 unresolved，不用相似图片或本机路径占位。

账号 schema 变化新增迁移并覆盖新建、升级、失败回滚和重试。DAO 独占 SQL；快照清理走公开 DAO 并
保护所有引用。

## 5. 新页面和公共组件

在 `src/features/<feature>/` 实现 Page/View 与 Controller，由 `src/ui/navigation.py` 注册导航，通过组合根
注入窄 dependencies。快捷入口使用导航 key，不使用堆叠页数字。跨功能复用先建立 Service、不可变 contract
或组合根公共组件，不经 MainWindow 字段和其他 Controller 转发。

## 6. 新算法、同步与装配方式

- 新算法复用官方 ID、固定快照、候选对象、配装槽位和保存方案 payload；输出包含真实 UID、目标位置、
  逐件评分、来源快照与算法/profile 版本。
- 多角色竞争由统一分配器处理，不用逐角色循环修改背包。
- 新同步方式提供等待、收集、完整性、稳定监听、单事务提交和明确错误状态；只提交完整快照。
- 新装配器只消费冻结的已保存方案，不在执行阶段重新优化；执行前校验来源、角色、槽位、UID 和位置，
  执行后产生可确认的新状态。
- 批量副作用使用持久任务、角色/槽位项和事件记录进度。
