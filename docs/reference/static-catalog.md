# 游戏资料库

“工具 → 游戏资料库”只读打开发行静态库，以一次冻结的领域与 dataset、schema、importer、构建时间映射为请求边界。
独立图鉴包的适用领域与原完整库的分工见[功能文档](../features.md#10-工具与游戏资料库)；包用途、来源优先级、
图片及晋升规则见[集成文档](../integrations.md#4-静态数据与资源)。全局搜索与专属详情都使用同一领域映射，
其中任一数据集改变都会使本次请求失效。
页面不读取账号库，不允许输入 SQL、表名或字段名，不启动战报 worker，也不改变任何配置。玩家搜索只消费
中文投影，不以 Gameplay Tag、GA、GE、Buff key 或资源路径补标题。

当前首页不提供“全部”、逐表覆盖总览、资源动画或来源追溯入口，而是按对象所有权组织五个玩家入口：角色、
弧盘、空幕与驱动、怪物与玩法、战斗机制图鉴。

- **角色数据**：角色身份、1–80 级面板、临界等级突破前后；普通攻击、E、Q、QTE 及正式存在的 G 按整行
  技能展示，允许编辑 1–10 级并在行内展开当前等级倍率。主动技能按正式目录逐条保留，不因空或重复效果标签遗漏。
  培养被动和固有能力按正式归属接在列表下方，解锁条件来自来源表；未提供时保留未知，不伪造技能等级。
  养成只分等级养成和技能养成；觉醒玩家顺序为一觉至
  六觉、三觉、六觉。等级养成按正式逐级经验、经验书规格和突破阶段汇总材料与甲硬币；经验书采用“先最少
  溢出、再最少本数”的确定折算，不换算副本次数或活力。技能养成直接汇总正式逐级 cost。
- **弧盘数据**：1–80 级经验与面板、突破材料/甲硬币、混频 1–5 级说明与参数、Buff、GE、触发和角色关系。
- **怪物与玩法**：图鉴、模板画像、争锋、轨外、副本、异象追猎和具有正式怪物池的高危委托。身份只能来自
  正式绑定、ID 或类路径，不从中文名猜测。
- **空幕与驱动**：展示空幕、驱动、套装、形状、属性和归属于该对象的二/四件套效果，关系优先在当前详情
  直接投影，不用连续跨页跳转拼信息。
- **战斗机制图鉴**：以直伤链、通用乘区、DOT 与环合、倾陷与结算四个玩家分类展示 28 条中文公式和
  非伤害状态规则。详情按“完整公式 → 计算顺序 → 变量来源 → 判定与限制”展开，明确区分攻击方、环合
  双方、公式胜出角色和受击目标。环合基础卡直接从发行静态库读取源档 0–15，并同时展示角色等级范围、
  创生、创生特殊项、浊燃、残虹浊燃和黯星基础值；倾陷卡另列角色等级 1–80 的逐级官方基础值。GA、GE、
  Buff、技能伤害叶子和反事实覆盖不作为卡墙条目；持续伤害仍以正式 `State.Damage.Dot` 作为领域判定，
  内部身份不进入玩家标题。

受支持版本的 schema 覆盖、资源关系及来源追溯仍作为内部完整性能力；空表和发行时省略的
`source_row.payload_json` 继续接受自动审计，但不进入玩家资料库菜单。

反事实矩阵和 C++ 差分组件仍属于内部审计及生产验证能力，不进入玩家资料库页面。

各领域字段、来源和已知缺口见下文各节。

## 内部覆盖审计

表清单、行数、schema 与来源覆盖以本次冻结的 `data/manifest.json` 和只读 SQLite 文件为准，不在文档维护
某次候选库的逐表数字。审计按正式领域和表身份记录空表、缺口与来源哈希；它是构建／晋升验证，不是玩家页面的
“覆盖总览”或任意 SQL 浏览器。内部查询使用固定领域 key、DAO 白名单和参数化值；大量来源行及资源关系按
对象 key 分页读取，不随基础详情一次加载。旧候选覆盖快照可从 Git 历史查阅，不作为当前发行事实。

## 游戏资料库正式术语与本地化边界

游戏资料库页面不得把数据库列名、内部枚举、资源路径或稳定 ID 当作玩家名称。角色、弧盘、空幕与驱动、
怪物与玩法、战斗机制图鉴统一通过 `StaticCatalogTerminologyService` 获取名称；页面只决定是否在正式名称旁
另列 GA、GE、item ID 等确有审计价值的专业标识。

### 解析顺序

1. 用 `entity_kind + stable_id + context` 查询只读发行静态源；别名必须先解析为 canonical ID。
2. 优先返回请求 locale；只允许回退到组合根显式配置的 locale。
3. 返回名称、显式 locale、`source_kind` 及原始 text table/key，供以后导入其他语言，不在页面复制中文字典。
4. 没有可读名称时返回 `status=name_missing` 且 `display_name=None`。页面应显示统一缺名状态，不能把 raw ID
   填进名称位置。

专业标识与名称是两个字段。比如 GA/GE 可以在详情审计区域展示，但 `GA_...`、`GE_...` 不能替代技能名或
效果名。

中央术语表的 `source_kind` 只有 `formal_localization`、`reviewed_annotation`、`ui_state`、
`name_missing`。玩家层只能使用公共可读投影“游戏内正式文本/审阅注解/界面状态/名称缺失”或省略来源，
不得显示这些 raw token。当前只读 DAO 支持的公共 `entity_kind` 为：`item`、`item_quality`、
`item_quality_color`、`character_acquisition_type`、`fork_campaign`、`damage_resistance`、
`outer_realm_fight_stage`、`character`、
`fork`、`fork_type`、`equipment_item`、`equipment_suit`、`equipment_attribute`、
`gameplay_ability`、`monster`、`clone_activity_category`、`clone_activity`、`feast_stage`。调用方不得
自行拼表或在未知 kind 上回退 raw ID。`gameplay_effect` 尚无可作为名称的正式字段，因此没有登记为可读名称
kind；GE ID 只保留为可选专业标识。

### 货币与养成成本

正式导出中大小写代表不同语义，禁止 casefold 后查询：

- canonical `Fons` 的名称来自 `/Game/Text/ST_Item.ST_Item` / `item_Fons_name`，简体中文为“方斯”；
- canonical `Gold` 的名称来自 `/Game/Text/ST_Ui.ST_Ui` / `gold_name`，简体中文为“甲硬币”；
- 养成来源中的 lowercase `gold` 与原生物品归档的 `ItemID=gold` 对应甲硬币；只有在各自明确的
  成本和背包语境下精确映射到 canonical `Gold`，不能并入 `Fons`。

别名解析严格采用“先查 context 下 exact alias，再查 exact-case canonical ID”。所以即使调用方传入
`progression_cost`，`gold` 和 `Gold` 都指向“甲硬币”，`Fons` 仍是“方斯”；context 不得吞掉或改写 canonical ID。

因此 UI 不再使用“金币”泛称，也不能仅凭字段名 `NeedGolds` 自创货币名。

### 当前正式名称来源

- 角色、弧盘、空幕/驱动、套装、技能和怪物：各规范化目录的 `name_zh` 与 text table/key；
- 装备属性：`equipment_attribute.display_name_zh/filter_name_zh`；
- 养成副本类目和玩法：`clone_activity_category.name_zh`、`clone_activity.name_zh`、
  `feast_stage(_difficulty).name_zh`、`abyss_level.name_zh`、`outer_realm_season_buff`；
- 六种角色属性：`ST_Common` 的 `character_reactionelementtype_02..07`；
- 环合状态：`ST_ReactionDes` 的 `Reaction_guangling`、`Reaction_zhouan`、`Reaction_anhun`、
  `Reaction_hunxiang`、`Reaction_guangxiang`、`Reaction_guanglingxiang`、`Reaction_zhouanhun`；
- 倾陷：`ST_TeachAndIllustrate` 的 `balance_name`。
- 弧盘 1–5 阶能力提升正式称“混频”：`ST_Common.ui_fork_star`、`ST_Ui.ui_forkupgrade_02` 和
  `ST_Ui.ui_forkdevelop_03`；不得把内部 star/refinement 命名直接显示为“精炼”。

尚未进入规范化静态表的 StringTable 术语应由后续 importer 统一导入。页面不得提前复制这些中文字符串；
导入前保持 `name_missing`，或只在明确的产品栏目标题中使用普通界面文案。

### 已确认的品质口径

`DataTable/Inventory/DT_ItemQuality.json` 同时给出玩家等级文本 `QualityText` 和颜色描述
`QualityDesc`。资料库应按展示对象选用正式字段，不能维护另一套颜色/等级字典：

- `ITEM_QUALITY_BLUE`：等级 B，颜色“蓝色”；
- `ITEM_QUALITY_PURPLE`：等级 A，颜色“紫色”；
- `ITEM_QUALITY_ORANGE`：等级 S，颜色“橙色”。

弧盘与角色使用 S/A/B 等级口径；装备可以展示等级，也可以展示正式颜色描述。不得把 ORANGE 自创为
“金色品质”，也不得把所有对象的品质强制成同一种文案。

### 当前覆盖与缺口

- 角色 `permanent/limited/free` 是稳定产品投影；成员关系单独存入
  `character_acquisition_membership`。常驻和限定成员来自正式 Lottery DataAsset，限定主推角色还以正式
  掉落组交叉验证；`free` 仍是审阅注解。成员关系与显示词不得混成一个事实；
- 八个限定弧盘特刊保存正式 pool ID、`UpList` 主推弧盘、`ShowText1` 的 text table/key，以及正式
  `PoolIDMap` 顺序。查询以倒序返回新到旧，不从页面私有字典或活动说明截取；
- 七种正式抗性名称来自 `ST_Common`：`chaos/cosmos/incantation/lakshana/nature/psyche/psychically`；
  `normal` 只有内部属性字段，必须保持 `name_missing`，禁止显示“普通抗性”；
- 轨外上下半场以完整 `EAbyssFightStage::FirstHalf/SecondHalf` 为 canonical ID，集中投影为 `ui_state` 的
  “上半场/下半场”；页面不得再用 `endswith` 自译。
- 已核对的角色技能、角色突破、弧盘突破和弧盘混频成本闭包中，除 lowercase `gold` 别名与空 sentinel
  `0` 外，全部 material item ID 都能在 `DT_ItemConfig` 通过 `ST_Item` key 取得正式中文名；
- `0` 不是物品 ID，必须在 importer 边界剔除；
- `gameplay_effect_catalog` 目前只有稳定 GE ID 和类路径，不能据此编造玩家效果名。没有所属技能/正式说明
  可提供名称时返回 `name_missing`；只有确需审计时才在专业标识字段显示 GE ID；
- Buff 的 duration、stacking、target 等内部枚举不是正式中文名称。可把正式结构化值投影到产品字段，但
  枚举 token 本身不得进入玩家标题或筛选项；
- 玩法内部 `clone_type`、敌人类路径、属性 enum 和资源路径都不是名称。应使用已导入的玩法、怪物、属性
  名称；缺少关系时保持缺名，不能从英文 token 猜中文。

明确禁止的映射包括：所有成本 token 按大小写不敏感地归并、`gold` 归并到 `Fons`；`NeedGolds` 直译成“金币”；ORANGE 统一写成
“金色”；把弧盘 star/refinement 显示成“精炼”；把 material/item ID、GA/GE ID、class path 或 enum tail
当作默认名称；根据中文说明反向猜不存在于正式关系中的术语。

## 游戏资料库：角色与养成数据域

本页描述“工具 → 游戏资料库”角色图鉴的只读数据边界。正式页面由资料库组合工厂创建，人物画廊、详情和返回动作复用同一领域入口；不保留旧表格详情面板或测试专用的产品入口。

### 分层与所有权

```text
StaticCatalogPage（公共导航与页面生命周期）
  → CharacterCatalogPage / CharacterCatalogPageController
  → CharacterDetailView（只持有展示状态）
  → StaticCatalogCharacterService（不可变、Qt 无关 DTO）
  → StaticCatalogCharacterQueries（参数化 SQL）
  → data/game_static.sqlite3（mode=ro）
```

- 输入只属于组合工厂选定并校验的发行静态或角色目录；不读取账号库、共享库、当前角色页或战报修改副本。
- DAO 是 SQL 的唯一所有者。搜索值、角色 ID、分页数量和偏移均参数化；UI 不接受任意 SQL、表名或排序字段。
- Service 保留正式 ID、枚举、资源路径和来源定位。中文属性标签仅是明确枚举 token 的展示投影，不替换原值。
- 页面 Controller 一次组合详情、成长、战斗关系、发行元数据与变体 DTO；View 通过 `set_data` 消费，先核对正式角色 ID 一致，不实现 SQL。
- 字段来源按规范化表和 `source_row → source_file` 标记；`source_payloads_omitted=true` 时不承诺原始 payload。

### 当前覆盖

| 能力 | 规范化事实表 | 展示口径 |
| --- | --- | --- |
| 角色目录、正式 ID、中文名、属性/组别、Actor 路径 | `character`、`character_annotation` | 包含可用、排期、变体与战斗变身，保留 classification |
| 1–80 级基础面板曲线 | `character_panel_growth` | HP/ATK/DEF；临界等级突破前后是独立行 |
| 20/30/40/50/60/70 突破阶段 | `character_panel_growth.state` | 只按正式 `breakthrough_before/after` 成对投影 |
| 好感度正式属性修改 | `character_likeability_bonus*` | 保留属性 ID、值、操作和百分比标记 |
| 六个普通觉醒与三/六觉共鸣 | `character_awaken_effect`、`character_awaken_skill_level_bonus` | 玩家顺序显示一觉至六觉、三觉、六觉；结构化事实保留共鸣类型，不从描述猜机制 |
| 技能目录、文本、等级、倍率曲线、升级条件与消耗 | `character_skill*`、`skill_damage`、`gameplay_ability_*`、`progression_item`、`localized_term*` | 普通攻击、E、Q、QTE 及正式 G 按整行展示；1–10 级可编辑，当前级倍率在行内抽屉显示；玩家层使用中文名和正式材料名，不显示 raw 技能身份 |
| 两项培养被动 | 官方 `PassiveAbilityList` 关系、`gameplay_ability_catalog`、`gameplay_ability_description` | 每个逻辑角色按突破 2/4 解锁；名称和说明只读正式中文，不把被动伪装成分级主动技能 |
| 培养阶段与推荐 | `character_cultivation_*` | 保留阶段人物/弧盘/空幕/驱动等级和技能推荐 |
| 毕业模板与专武/空幕关联 | `character_graduation_template`、`fork_item`、`equipment_suit` | 明确模板来源、生成时间、正式 ID 和资源路径 |
| GA → GE/Buff 与角色所属 Buff | `character_combat_ability_binding`、`combat_ability_effect_binding`、`gameplay_effect_catalog`、`buff_definition` | Service 支持分页；当前详情读取一页关系并保留事件 Tag、GE index、Class/Asset 路径，不据此宣称所有绑定已加载 |
| 名称、ID、GA、GE、Buff、资源路径搜索 | 上述关系表 | 搜索只用于定位所属角色；`%`、`_` 按字面量处理 |

### 养成材料与降级

- schema v31 从 `DT_CharacterUpgradeDataTable` 保存共享的 1–80 逐级 `NeedExp`，从角色正式
  `UpgradePackId` 建立养成档案；区间总经验按当前等级对应的行累加到目标等级前一行。
- `DT_ItemConfig` 中三种正式角色经验书保存经验值及每次使用的甲硬币成本。页面以总经验为目标，先最小化
  经验溢出，再最小化本数；该折算是确定的材料组合，不读取账号库存，也不换算副本或活力。
- `DT_CharacterBreakthroughDataTable` 的 0–6 阶、人物等级上限、正式材料和甲硬币全部规范化；只有玩家选择
  “包含沿途突破”时，才汇总当前与目标等级之间尚需跨过的阶段。
- 人物与技能成本中的 lowercase `gold` 只在 `progression_cost` 语境规范成 Gold/甲硬币；显式
  `Fons` 仍为方斯。技能升级消耗继续通过 `progression_item`、`localized_term*` 解析玩家名称；缺名时显示
  “名称暂未提供”，raw ID 不进入默认界面。
- 没有独立正式养成包的目录条目保持 unavailable，不从同名角色、说明文字或外部 JSON 猜材料。
- 战斗变身或未完整导入角色可能只有目录/战斗绑定，没有人物面板、好感度、觉醒、培养或毕业模板；Service
  以 `CatalogGap` 返回缺失，不回退同名角色，也不通过中文名或 Actor 路径合并。
- 毕业模板属于构建期派生静态事实，来源标记为 `character_graduation_template.source_kind`，不冒充原始官方行。

### 集成接线

`features/static_catalog/dependencies.py` 创建只读 Queries、Service、术语与发行元数据依赖，通过
`domain_pages/characters/page.py` 的公开工厂构造页面，关闭回调负责 DAO 资源收尾。
`CharacterCatalogPageController.profile` 按正式 ID 读取详情及成长／战斗关系；`CharacterDetailView.set_data`
拒绝角色身份不一致的组合。这里是当前同步读取链，不描述为尚未实现的异步分页或旧回包 setter。
成长、技能和培养推荐视图分别归 `characters/growth.py`、`skills.py`、`builds.py`；公共壳负责跨领域链接和返回。
本域不消费账号状态，不创建账号 generation；静态目录用途、来源与更新边界遵循 [静态数据与资源](../integrations.md#4-静态数据与资源)。

## 游戏资料库：弧盘数据域

本页记录“工具 → 游戏资料库”的弧盘域只读边界。它消费本次打开时冻结的发行静态库；安装独立
`reference`/`role_page` 图鉴包时使用 `data/role_catalog/game_static.sqlite3`，否则使用
`data/game_static.sqlite3`。一次请求不逐行回退，不读账号库，不写静态库，不启动后台任务或游戏集成。

### 展示来源分类

| 标记 | 含义 | 弧盘域中的例子 |
| --- | --- | --- |
| `official_static` | importer 从正式数据集保留的字段 | 弧盘 ID、名称、等级 modify pack、突破消耗、混频参数、Buff/GE 资产路径 |
| `project_projection` | importer 对正式行所做的项目结构化投影 | `combat_effect_definition`、`combat_effect_buff_link`、跨表关系跳转 |
| `derived_display` | Service 为界面组合的值，不是新的游戏事实 | 20/30/40/50/60/70 级“突破前/突破后”的等级行 + 突破阶段行组合、百分比格式化 |

界面不从中文说明猜数值。临界等级通过公共
`advancement_stage_service.fork_active_panel_stats()` 选择明确的突破阶段，并组合该等级的
`fork_upgrade_level` / `fork_modify_value` 与对应阶段的 `fork_breakthrough` /
`fork_modify_value`，再加入当前精炼的 `fork_permanent_property`；20/30/40/50/60/70 级同时保留突破前和突破后两个节点。

### 图鉴组织与角色适配

首屏使用 `data/role_catalog/game_ui/forks` 和 `GameUiAssetCatalog` 的正式图标渲染全部 49 件弧盘，不创建数据库表格。
8 件限定特刊只消费 `StaticCatalogTerminologyService.list_fork_campaigns()` 返回的 DB-backed
`LocalizedForkCampaign`：正式 `featured_fork_id` 决定限定分组，`release_ordinal` 决定新到旧顺序，
`title.display_name` 决定多语言标题；标题缺失时显示“名称暂未提供”。页面不保留限定 ID、标题或顺序的
私有字典。其余 41 件属于同一首发批次，按品质、类型和名称稳定排列。类型筛选只改变当前卡片投影，不改变
数据或顺序事实。

Campaign 的 canonical identity 是 `pool_id`；`featured_fork_id` 只是关联弧盘，不是 campaign 主键或唯一
身份。同一弧盘未来存在多次复刻时，页面保留全部 campaign records，限定分组仍按关联成立，弧盘卡片与详情
展示其中 `release_ordinal` 最新的一条，排序也取该弧盘最高正式 ordinal，不用字典覆盖较新记录。

名称/正式 ID 搜索常驻首屏；品质与类型筛选收纳在默认折叠的筛选面板中。卡片墙按可用宽度在 1–5 列之间
自动重排，窄窗口不保留固定四列，也不产生横向滚动。筛选只作用于当前只读投影，清空后恢复完整正式顺序。

养成消耗和品质名称必须经过组合根注入的 Qt-free `StaticCatalogTerminologyService` 投影。页面不拥有货币、
物品或品质的中文映射；`ForkItemDisplayNameService` 只消费公共术语结果并生成玩家可读名称。
缺少正式名称的物品统一显示“名称暂未提供”，数量仍保留。玩家页面不展示 raw item ID、资源路径或来源
枚举；View 不硬编码或猜测物品名称。

详情固定展示“详情”和“养成”两个页签。详情顶部只保留紧凑的弧盘大图、名称、品质、类型和归属，不使用
占满横向空间的大看板，也不向玩家展示正式 `fork_id`。页面允许
在 1–80 级、临界突破前后和混频 1–5 级之间切换，联动展示面板、混频技能与效果关系。混频参数直接代入
正式技能描述；结构化效果只投影为玩家可理解的生效时机、效果内容、叠加规则和生效条件。界面不展示
dataset/schema/importer、资源或动画路径、GE/Calculation/requirement 路径、内部枚举和来源哈希。缺少可解释
效果时显示“暂无额外效果说明”，不显示伪造的零值。

每件弧盘都按正式 `group_type` 展示全部同类型可用角色；独占角色和养成推荐关系继续由
`exclusive_character_ids_json` 与 `character_cultivation_fork_recommendation` 标记，角色使用
`GameUiAssetCatalog.character_icon()` 的正式头像。没有独占/推荐关系不妨碍展示同类型可用角色，但不得把
同类型角色写成专属角色。角色头像与名称、混频效果的触发条件、效果内容和叠加规则直接在弧盘详情内呈现，
不再用跨页面跳转按钮代替关系内容。

### 养成材料展示

养成页只读展示当前所选等级的升级经验与突破消耗、0–6 阶完整突破路线，以及混频 1–5 级的正式消耗。
schema v36 另外通过 `fork_exp_material` / `fork_exp_material_cost` 展示淡色、无彩和混沌染剂的正式图片、
单件经验与使用甲硬币；突破阶段的材料和甲硬币同样使用 `progression_items` 图片卡片，不从描述文本提取数值。
材料和甲硬币都由公共术语目录投影正式名称与数量；缺名或缺量保持“名称暂未提供”或“暂未提供”，不显示 raw
ID，也不伪装成 0。页面不提供“当前 → 目标”、材料缺口、账号库存、副本次数或活力计算，不公开养成请求
信号和结果回填接口。

### 当前发行只读审计

当前主库来自同一正式服导出，经完整构建和正式晋升；精确 dataset、schema、importer、大小与哈希以
`data/manifest.json` 为准。独立图鉴以 `data/role_catalog/manifest.json` 为准，不向主库混入测试服数据。
角色、弧盘、技能、Buff 和敌人均按正式 ID、资产绑定及来源哈希关联。

| 内容 | 权威表 / 字段 | 覆盖规则 |
| --- | --- | --- |
| 弧盘目录与资源 | `fork_item`, `fork_type` | 正式品质、类型、icon/card/painting |
| 角色关联 | `exclusive_character_ids_json`, `character_cultivation_fork_recommendation` | 仅正式独占 ID 或养成推荐 |
| 升级、突破、混频 | `fork_upgrade_level`, `fork_breakthrough`, `fork_star_level` | 正式等级、属性包、消耗和占位参数 |
| 常驻面板 | `fork_permanent_property` | 按弧盘、精炼和来源参数唯一投影 |
| Buff、触发与 GE | `buff_definition`, `buff_modifier`, `buff_trigger_effect`, `gameplay_effect_catalog` | 精确资产路径关联，不由说明猜倍率 |
| 材料、本地化与染剂 | `progression_item`, `localized_term*`, `fork_exp_material*` | 正式物品身份、名称、经验和成本 |
| 排期和季节增益 | `outer_realm_rotation*`, `outer_realm_season_buff*` | 官方排期与明确维护者补注分开保存；受益对象证据见战斗覆盖文档 |
| 来源追溯 | `source_row`, `source_file` | 文件及行哈希；发行库省略原始 payload |

弧盘行来源包括 `DataTable/Fork/DT_ForkItemData.json`、`DT_ForkUpgradeData.json`、
`DT_ForkBreakthroughData.json`、`DT_ForkUpgradeStarDataTable.json`、`CT_ForkBuff.json` 以及
`DataTable/PackData/ModifyData/DT_ForkModifyData.json`。

### 当前 importer 边界

- 当前 schema 没有 `fork_skill` / `fork_skill_level` 表。产品中的“弧盘技能”只能展示混频
  1–5 级描述、参数、Buff 与效果，不可声称另有独立技能目录。
- `source_payloads_omitted=true`；发行库不能还原原始 JSON 全行，只能展示 importer 保留字段、
  来源文件和校验值。
- 弧盘到 GA 没有结构化直接绑定。DAO 只在资产路径精确匹配时返回 GA；当前发行库
  审计为 0，不从描述文本、名称或路径前缀推测。
- 有正式独占角色 ID 或养成推荐关系的弧盘显示对应徽记，其余不显示；所有弧盘仍可依据
  正式 `group_type` 展示同类型可用角色，不按中文名、描述或效果类型猜专属角色。
- 当前 schema 已提供通用养成物品和本地化目录；突破消耗通过稳定 item ID 关联正式名称。缺名仍显示
  “名称暂未提供”，玩家页面不展示 raw ID。
- `combat_effect_definition` 和 `combat_effect_buff_link` 是 importer 正规化的项目投影；
  其中描述、参数和 Buff 路径仍可回溯正式行，但投影 ID 不标成官方原始字段。
- `fork_permanent_property` 先按正式属性 ID 与精炼参数完整标识关联；名称简写时，只在每级第一参数和唯一
  无条件直接 Modifier 同时成立时采用结构关联。施加条件、来源/目标标签、同级多候选或精炼曲线不完整均不猜值，
  并进入构建审计。子 Buff 未声明本地 Modifier 时会沿正式 Blueprint `Super` 链读取最近父级 Modifier；当前审计为
  20 件已解析、5 件仅有条件候选、24 件没有直接计算证据。
  触发、层数、持续时间和状态限制效果继续由战斗状态链路处理。
- schema v36 将 20 件弧盘的 100 条闭合结果持久化，并据此重新生成 22 份毕业模板；运行时不读取仓库外
  `Content` 文件，也不新增账号配置或手工入口。

### 分层与集成接口

```text
ForkCatalogPage → ForkProfileView
    ↓
StaticCatalogForkService + StaticCatalogCharacterService（Qt 无关冻结 DTO）
    ↓
现有参数化只读 DAO（复用 StaticGameDataDao mode=ro/schema 校验）
    ↓
data/game_static.sqlite3
```

域模块本身不修改公共导航。集成任务需要：

1. 调用公开 `build_fork_catalog_page(database_path=..., game_ui_asset_root=...,
   terminology_service=...)` 创建独立页面；公共术语 Service 是必需注入，factory 从它加载一次冻结的
   campaign records，不创建第二个术语 DAO。factory 只组合现有只读 DAO/Service 和资源目录，不修改公共导航。
2. 把返回的 `ForkCatalogPage` 放入“游戏资料库”公共 Page，并在生命周期结束时调用 `dispose()`。
   页面会尝试关闭全部已拥有 Service；单个关闭失败不会跳过后续 owner，最终以 `ExceptionGroup` 暴露错误。
   已成功关闭的 owner 从待关闭集合移除，后续重试只调用失败项，全部完成后 `dispose()` 幂等。
3. 公共导航仍由集成任务一次性添加 `parent_key="toolbox"` 的“游戏资料库”子页；
   本域不访问 `MainWindow`、`toolbox/page.py` 或其他并行数据域。

## 游戏资料库：怪物与玩法域

本域只读发行静态库 `data/game_static.sqlite3`，并读取代码内有公开来源的活动排期注解；不读取账号库或战报，
不修改静态数据，不按
中文名猜测怪物 ID。“工具 → 游戏资料库”组合根已通过公开
`build_monster_catalog_page(...)` 工厂注入 `StaticCatalogMonsterService`、公共术语服务与游戏 UI
资源目录，构建独立 `MonsterCatalogPage`。页面公开 `open_record(record_id)` 供资料库容器打开正式记录；
成功时返回 `True`，记录缺失时返回 `False`，以便资料库容器回滚跨域导航。页面内部的玩法、期数、层级、
怪物与详情返回由自身导航层级管理。

### 数据边界

| 展示域 | schema v30 事实源 | 当前发行库覆盖 |
| --- | --- | ---: |
| 官方图鉴 / 大世界 | `monster_catalog`、`monster_identifier_alias` | 35 个图鉴对象 |
| 怪物模板与等级画像 | `monster_instance_profile`、`monster_instance_profile_variant` | 4,311 个模板画像 |
| 图鉴与模板身份绑定 | `monster_template_binding` | 85 条显式绑定 |
| 异象追猎单 Boss | `monster_catalog.enemy_type='WeeklyBoss'` 与 `world_boss_id` | 7 个 Boss |
| 争锋赏宴 | `feast_stage*`、`clone_activity*`、怪物等级画像与公开排期注解 | 按当前冻结 dataset 的挑战、各难度全部正式目标与画像读取，不固化行数 |
| 轨外之境 | `outer_realm_rotation`、`abyss_level*`、`abyss_monster_pool_entry` | 12 个编号配置，含层/上下半场/怪物池；其中 6 个有正式排期 |
| 材料与养成副本 | `clone_activity*`、`clone_spawn_member` | 56 个活动、218 个难度；159 条展示于冒险手册、59 条隐藏，68 条没有类目 ID |
| 高危委托 | `high_risk_commission*`、`high_risk_monster_pool_member` | 13 个委托、78 个难度 |
| 数值画像 | `enemy_combat_profile`、`enemy_element_resistance` | 生命、防御、倾陷、8 类抗性 |
| 来源追溯 | `source_row` → `source_file` | 相对路径、行键、内容/文件 SHA-256 |

发行 manifest 标记 `source_payloads_omitted=true`；来源键与摘要只用于底层导入审计，不进入玩家主界面，
也不声称可查看完整原始 payload。当前 schema 没有怪物攻击属性/攻击档字段；界面显示“不可用”，不从等级、中文说明
或战报残差反推。高危委托只有通用回退池而没有逐难度怪物池时，同样明确标记不可用。

### 事实类型

- **官方静态事实**：正式图鉴 ID、模板 ID、类路径、玩法配置、难度、刷怪槽位和来源键。
- **项目显示注解**：直接模板没有图鉴绑定时，汇总同一正式 `mon/boss` 数字家族在图鉴绑定、争锋 Boss
  配置和轨外刷怪记录中的中文名；全部有效名称一致时，可补充该名称用于卡片和详情标题。它不建立当前模板
  与图鉴的身份关系，也不生成关系跳转；存在多个名称时仍保持不可用。
- **等价公式画像**：`profile_set + pack_id` 连接的生命、防御、倾陷和抗性。多个身份可以共用
  同一画像，共用画像不证明怪物身份。
- **项目派生 / 注解**：“当前期 / 下一期”、有公开官方公告的活动排期与成员顺序、字段不可用原因及展示标签。
  它们不回写静态库，也不进入战报自动候选。

身份与玩法关系只使用 `monster_template_binding`、玩法表中的正式模板 ID，或正式 Unreal 类路径的对象名，
并由当前玩法页直接渲染为所属期数、层、半场、刷怪槽位和画像摘要；不把普通钩稽关系做成跨页跳转。
Buff 与玩法规则同样在当前页完整展示名称、说明、数值与生效条件，不提供机制跳转按钮。底层正式机制 ID
仅保留作导入与数据一致性审计。争锋赏宴内嵌七项正式魔女赐福选择及效果摘要，但不把赐福并入敌方
挑战条件或画像。任何关系都不使用中文名、数值画像相似或战报推断来建立怪物身份。

### 争锋赏宴活动期

新版 `MechanismOptionChoices[].OptionID` 独立于普通条件分类：每个挑战目标独立记录选择，保留正式说明、目标分值、
关卡 `MechanismScoreLimit` 和全部玩家／敌方效果来源。选择仅记录场景条件，不证明目标完成，不推算结算得分；
触发时序与受体缺证据时，不从描述或 Buff 路径生成常驻攻防，也不自动勾选这些目标。

目标按正式副本难度的 `SpawnID → clone_spawn_member → monster_instance_profile` 绑定；预览模型数组不承担战斗身份。
同一关卡可以有多个目标，各自保留等级、属性包与来源。资料页明确选择当前画像，战报配置明确选择主要目标并冻结全部成员画像；
多目标未选择时不默认取第一项，来源缺失或歧义不按模型名猜测。测试来源使用独立“排期待确认”入口，不套用正式服旧活动期和同名关卡的历史数值。

争锋赏宴先展示活动期，再展示该期挑战对象；`DiyBossStage1` 等是挑战 ID，不是活动期数。大陆服已确认：

- 1.3 期：2026-08-13 版本更新后至 2026-09-24 05:59，共 8 个挑战。排期与新增玛门、墨菲克斯来自
  [1.3 版本更新公告](https://yh.wanmei.com/news/gamebroad/20260812/263538.html)。
- 1.1 往期：2026-06-12 10:00 至 2026-07-02 05:59，共 7 个挑战。排期来自
  [1.1 版本更新公告](https://yh.wanmei.com/m/news/gamebroad/20260527/262372.html)，成员顺序由同期实机界面确认。

往期第二个挑战“随心所欲”使用静态库仍保留的 `DiyBossStage111 → Boss_016_BP_DiyBoss` 正式关系、
`clone_level` 四档等级与 `profile_set + pack_id` 画像，并按正式 Boss 数字家族解析 `Boss_16` 图片。
不能按“第二个挑战”猜成 `UI_YH_PVEBOSS_BOSSList_Boss_002`。其往期挑战条件成员关系未保留，因此界面
不套用当前期条件；其他仍在当前正式表中的往期挑战继续读取同一正式挑战记录。往期资料只属于游戏资料库，
不把已结束活动重新加入战报当前正式候选。

### 活动期状态口径

查询时把上海时区的当前时刻与 `starts_at_mainland` / `ends_at_mainland` 比较：

1. 时刻在开始和结束之间（含边界）的配置是“当前期”。
2. 当前时刻之后开始时间最早的配置是“下一期”。
3. 其他配置标记为“已结束”或“待开放”。
4. 没有正式开始/结束时间的编号配置标记为“未提供排期”，仍展示正式层、上下半场、怪物池成员、中文名与公式画像。

`inference_ordinal` 作为官方导入字段展示，不代替大陆服生效时间判定。
`Abyss_Common` 是公共基础配置，不作为编号期数展示。未排期配置不能依靠编号、相邻周期或赛季顺序补猜日期，
也不进入只接受当前/下一期的战报自动候选。

轨外怪物详情以关卡、半场、刷怪顺序、怪物池和怪物顺序共同定位，直接消费该成员正式
`profile_set + pack_id` 的生命、防御、倾陷和抗性画像。资料卡标题保留怪物池正式中文名；模板详情只作为
正式类路径对象名的补充关系，不能覆盖本次出场画像。图片仅按正式模板数字家族和随程序提供的官方头像资源解析。

## 战斗机制图鉴

“工具 → 游戏资料库 → 战斗机制图鉴”面向玩家解释每一种伤害或状态怎样计算。页面采用与战报逐击详情一致
的解释顺序，但只展示机制本身，不读取账号战报，也不把示例值冒充当前实战值。

### 玩家分类与内容

卡墙按四个稳定领域展示 28 条中文公式或状态规则：

- **直伤链**：角色面板、技能倍率、直伤总公式；
- **通用乘区**：增伤、易伤、暴击、防御、抗性；
- **DOT 与环合**：通用 DOT、噩梦、蚀心、鸩火、环合归属与 16 档基础值，以及创生、覆纹、浊燃、黯星、
  浸染、延滞、盈蓄、失谐；
- **倾陷与结算**：独立最终增伤、团队倾陷、达芙蒂尔额外倾陷、最终向下取整、最大生命结算、安魂曲五觉生命
  上限削减和法帝娅共享伤害。

GA、GE、Buff、Gameplay Tag 和技能伤害叶子不作为卡墙条目。它们仍可作为公式输入证据，但玩家页面只使用
中文机制名和中文变量说明。“鸠火”作为常见误写只进入搜索别名，正式标题保持“鸩火”。

### 详情结构

每张公式卡固定展示：

1. **完整公式**：完整乘区顺序和唯一最终向下取整出口；
2. **计算顺序**：从身份、状态到最终结算的逐步过程；
3. **变量来源**：逐项说明取攻击方、两名环合参与者、比较胜出角色、实际伤害来源还是受击目标；
4. **判定与限制**：列出不消费的乘区、状态轴要求和仍缺少的证据。

直伤变量明确拆为攻击方技能/面板/增伤/暴击/穿透，以及受击目标防御/抗性/减益/易伤。DOT 层数始终按
`(半场, 目标身份)` 读取命中前状态；有正式 `State.Damage.Dot` 身份就是 DOT，不由手工渠道白名单决定。

### 环合归属与官方基础值

创生、黯星、浊燃比较两名环合参与者各自的“环合强度 × 角色等级基础值”并取较高者。覆纹和浸染不使用
这条双方比较规则：覆纹逐条读取被记录原伤害的实际来源角色；浸染逐条读取 12 秒区间内当前受益伤害
自己的来源角色。QTE 角色只负责触发对应环合，不固定提供覆纹记录或浸染后续逐击的环合强度。

该边界来自正式本地化属性说明 `ST_Common::Mag_detail`：“创生、黯星、浊燃”取双方最终效果较高者，
“覆纹、浸染”取造成伤害者。资料库和战报不得把后半句改写成 QTE 触发者或双方取高。

环合基础卡从只读发行静态库读取源档 0–15，不在 UI 硬编码数值：

```text
源档 = floor((胜出角色等级 - 1) / 5)
源档 0  = 角色等级 1–5
源档 15 = 角色等级 76–80
```

每一档同时列出创生、创生特殊项、浊燃、残虹浊燃和黯星基础值。资源名后缀不能证明角色归属，因此第二条
创生曲线只显示为“创生特殊项”。残虹浊燃有独立正式曲线；当前 16 档虽与普通浊燃相同，图鉴仍分列展示，
不因数值相同合并来源。

倾陷不套用上述 16 档。它从只读发行静态库读取角色等级 1–80 的逐级正式基础值，并按每 10 级一个清晰区块
完整展示；1 级为 91，80 级为 3,603。团队倾陷公式中的每名角色都按自己的等级读取该曲线，不能只取触发
角色，也不能先把全队属性合并。

### 覆纹复制部分的拆分展示

覆纹详情将基础复制比例、灵可复制比例加成和追击增伤分开说明，同时展示弱点感应未启用与启用的数值。
追击增伤只作用于复制部分，原伤保持实际结算值，强度附加项单独相加。该写法是当前结果的拆分解释，
不作为游戏内部计算顺序或其他追击增伤来源适用性的证明；具体公式、取值和核心边界见
[环合基础规则](damage/README.md)。

### 浸染闭合公式

浸染不是独立伤害，也不是 12 秒后统一结算。魂+相 QTE 只建立或刷新 12 秒状态；区间内每条受益逐击
分别读取该击实际伤害来源角色的环合强度：

```text
浸染最终乘区
= 1.20 × [1 + 20% × 本击来源环合强度 / (本击来源环合强度 + 180)]

受益逐击 = 原完整伤害 × 浸染最终乘区
```

区间从触发 QTE 结算后开始，持续 12 秒，后续触发刷新；触发 QTE 本击不消费本次浸染。区间内队伍所有
角色的后续逐击都受益，但乘区按各击来源角色分别计算，不是全队共享同一个数值。固定轴不能确认某击的
实际伤害来源时，状态可以展示，但该击环合强度收益保持未解析，不能回退到 QTE 触发者、双方较高值或
相邻伤害。团队倾陷和非逐击最大生命结算当前不默认消费该乘区。

### 展示与只读边界

卡片正文、筛选按钮、变量标签和公式字号均不小于 11 像素；常用宽度最多三列，窄屏切换为四项完整下拉并
回落为单列。详情用平面分段展示，不使用嵌套技术看板或内部来源按钮。

页面只读发行静态库，不读取账号库、不启动战报 worker、不选择反事实执行器。反事实四态、C++ 差分组件、
来源路径和哈希仍属于内部审计能力；图鉴展示不改变战报计算与逐击重放的生产语义。
