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
| 六个普通觉醒与三/六觉共鸣 | `character_awaken_effect`、`character_awaken_skill_level_bonus` | JSON 结构展开为路径和值；不从描述猜机制 |
| 技能目录、文本、等级、升级条件与消耗 | `character_skill*`、`gameplay_ability_*` | 材料显示正式物品 ID 和数量；保留 GA、Tag 和资源路径 |
| 培养阶段与推荐 | `character_cultivation_*` | 保留阶段人物/弧盘/空幕/驱动等级和技能推荐 |
| 毕业模板与专武/空幕关联 | `character_graduation_template`、`fork_item`、`equipment_suit` | 明确模板来源、生成时间、正式 ID 和资源路径 |
| GA → GE/Buff 与角色所属 Buff | `character_combat_ability_binding`、`combat_ability_effect_binding`、`gameplay_effect_catalog`、`buff_definition` | 独立分页，保留事件 Tag、GE index、Class/Asset 路径 |
| 名称、ID、GA、GE、Buff、资源路径搜索 | 上述关系表 | 搜索只用于定位所属角色；`%`、`_` 按字面量处理 |

## 明确缺失与降级

- `schema v29` 没有规范化的人物升级经验、材料或金币消耗表。
- `schema v29` 只有突破前后面板，没有规范化的人物突破材料或金币消耗表。
- 技能升级消耗有正式物品 ID 与数量，但没有通用材料物品中文目录，因此材料名保持 ID，不按 ID 字面猜中文名。
- 战斗变身或未完整导入角色可能只有目录/战斗绑定，没有人物面板、好感度、觉醒、培养或毕业模板；Service
  以 `CatalogGap` 返回缺失，不回退同名角色，也不通过中文名或 Actor 路径合并。
- 毕业模板属于构建期派生静态事实，来源标记为 `character_graduation_template.source_kind`，不冒充原始官方行。

## 集成接线

集成任务应在组合根创建 `StaticCatalogCharacterQueries` 和 `StaticCatalogCharacterService`，由 Controller 负责关闭
DAO、处理异常和丢弃旧选择回调。详情选择时只调用 `get_character_detail(character_id)`；切换到成长或战斗关系页
后再分别调用 `list_growth`、`list_combat_links`。Controller 必须把回包的 `character_id` 与当前选择复核后，调用
`CharacterDetailPanel.set_growth_page` 或 `set_combat_page`。页面退出时关闭只读 DAO；不持有账号 generation，
因为本域不消费账号状态。
