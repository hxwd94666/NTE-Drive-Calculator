# 游戏资料库正式术语与本地化边界

游戏资料库页面不得把数据库列名、内部枚举、资源路径或稳定 ID 当作玩家名称。角色、弧盘、空幕与驱动、
怪物与玩法、战斗机制图鉴统一通过 `StaticCatalogTerminologyService` 获取名称；页面只决定是否在正式名称旁
另列 GA、GE、item ID 等确有审计价值的专业标识。

## 解析顺序

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

## 货币与养成成本

正式导出中大小写代表不同语义，禁止 casefold 后查询：

- canonical `Fons` 的名称来自 `/Game/Text/ST_Item.ST_Item` / `item_Fons_name`，简体中文为“方斯”；
- canonical `Gold` 的名称来自 `/Game/Text/ST_Ui.ST_Ui` / `gold_name`，简体中文为“甲硬币”；
- 角色技能、角色突破和弧盘养成源使用 lowercase `gold` 成本 token，但物品表与资本物品表均没有该
  canonical ID。它只在 `progression_cost` 上下文解析到 `Fons`，不能解析到 `Gold`。

别名解析严格采用“先查 context 下 exact alias，再查 exact-case canonical ID”。所以即使调用方传入
`progression_cost`，`gold` 仍是“方斯”、`Gold` 仍是“甲硬币”；context 不得吞掉或改写 canonical ID。

因此 UI 不再使用“金币”泛称，也不能仅凭字段名 `NeedGolds` 自创货币名。

## 当前正式名称来源

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

## 已确认的品质口径

`DataTable/Inventory/DT_ItemQuality.json` 同时给出玩家等级文本 `QualityText` 和颜色描述
`QualityDesc`。资料库应按展示对象选用正式字段，不能维护另一套颜色/等级字典：

- `ITEM_QUALITY_BLUE`：等级 B，颜色“蓝色”；
- `ITEM_QUALITY_PURPLE`：等级 A，颜色“紫色”；
- `ITEM_QUALITY_ORANGE`：等级 S，颜色“橙色”。

弧盘与角色使用 S/A/B 等级口径；装备可以展示等级，也可以展示正式颜色描述。不得把 ORANGE 自创为
“金色品质”，也不得把所有对象的品质强制成同一种文案。

## 当前覆盖与缺口

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

明确禁止的映射包括：`gold` 按大小写不敏感命中 `Gold`；`NeedGolds` 直译成“金币”；ORANGE 统一写成
“金色”；把弧盘 star/refinement 显示成“精炼”；把 material/item ID、GA/GE ID、class path 或 enum tail
当作默认名称；根据中文说明反向猜不存在于正式关系中的术语。
