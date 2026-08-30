# 游戏资料库：角色与养成数据域

本页描述“工具 → 游戏资料库”中角色数据域的当前只读边界。页面接线由组合根集成任务负责；本域不修改导航、
`src/ui/app.py`、工具页或公共 `static_catalog` 导出。

## 分层与所有权

```text
StaticCatalogPage / Controller（集成任务）
  → CharacterDetailPanel（只持有展示和分页状态）
  → StaticCatalogCharacterService（不可变、Qt 无关 DTO）
  → StaticCatalogCharacterQueries（参数化 SQL）
  → data/game_static.sqlite3（mode=ro）
```

- 输入只属于发行静态数据集；不读取账号库、共享库、当前角色页或战报修改副本。
- DAO 是 SQL 的唯一所有者。搜索值、角色 ID、分页数量和偏移均参数化；UI 不接受任意 SQL、表名或排序字段。
- Service 保留正式 ID、枚举、资源路径和来源定位。中文属性标签仅是明确枚举 token 的展示投影，不替换原值。
- View 只消费 DTO，并通过 `growth_page_requested`、`combat_page_requested` 请求 Controller 加载下一页。
- 字段来源按规范化表和 `source_row → source_file` 标记；`source_payloads_omitted=true` 时不承诺原始 payload。

## 当前覆盖

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
| GA → GE/Buff 与角色所属 Buff | `character_combat_ability_binding`、`combat_ability_effect_binding`、`gameplay_effect_catalog`、`buff_definition` | 独立分页，保留事件 Tag、GE index、Class/Asset 路径 |
| 名称、ID、GA、GE、Buff、资源路径搜索 | 上述关系表 | 搜索只用于定位所属角色；`%`、`_` 按字面量处理 |

## 养成材料与降级

- schema v31 从 `DT_CharacterUpgradeDataTable` 保存共享的 1–80 逐级 `NeedExp`，从角色正式
  `UpgradePackId` 建立养成档案；区间总经验按当前等级对应的行累加到目标等级前一行。
- `DT_ItemConfig` 中三种正式角色经验书保存经验值及每次使用的方斯成本。页面以总经验为目标，先最小化
  经验溢出，再最小化本数；该折算是确定的材料组合，不读取账号库存，也不换算副本或活力。
- `DT_CharacterBreakthroughDataTable` 的 0–6 阶、人物等级上限、正式材料和方斯全部规范化；只有玩家选择
  “包含沿途突破”时，才汇总当前与目标等级之间尚需跨过的阶段。
- 人物与技能成本中的 lowercase `gold` 只在 `progression_cost` 语境规范成 Fons/方斯；不会映射成
  Gold/甲硬币。技能升级消耗继续通过 `progression_item`、`localized_term*` 解析玩家名称；缺名时显示
  “名称暂未提供”，raw ID 不进入默认界面。
- 没有独立正式养成包的目录条目保持 unavailable，不从同名角色、说明文字或外部 JSON 猜材料。
- 战斗变身或未完整导入角色可能只有目录/战斗绑定，没有人物面板、好感度、觉醒、培养或毕业模板；Service
  以 `CatalogGap` 返回缺失，不回退同名角色，也不通过中文名或 Actor 路径合并。
- 毕业模板属于构建期派生静态事实，来源标记为 `character_graduation_template.source_kind`，不冒充原始官方行。

## 集成接线

集成任务应在组合根创建 `StaticCatalogCharacterQueries` 和 `StaticCatalogCharacterService`，由 Controller 负责关闭
DAO、处理异常和丢弃旧选择回调。详情选择时只调用 `get_character_detail(character_id)`；切换到成长或战斗关系页
后再分别调用 `list_growth`、`list_combat_links`。Controller 必须把回包的 `character_id` 与当前选择复核后，调用
`CharacterDetailPanel.set_growth_page` 或 `set_combat_page`。页面退出时关闭只读 DAO；不持有账号 generation，
因为本域不消费账号状态。
