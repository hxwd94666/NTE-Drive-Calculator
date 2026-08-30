# 游戏资料库全库覆盖清单

本清单按产品领域审计发行静态库 schema v30 的全部 124 张业务表。“覆盖总览”固定登记并显示这些表的
名称、行数、领域和 A–E 状态，其他产品目录仍使用角色、弧盘、装备、技能、效果、怪物、资源和来源等领域
名。页面不提供任意 SQL、字段输入或原始表浏览器。

角色、弧盘、怪物和玩法由各自窄 Service/DAO 拥有；装备、技能效果、资源关系和来源追溯也保持独立查询
边界，公共资料库只做 Qt 无关契约适配和只读组合。行数来自 dataset `cn_1_3_13_20260828` 的只读审计。

标记约定：

- **展示**：有用户入口，可搜索或从关系跳转进入。
- **合并详情**：不作为独立搜索结果，在所属实体详情中展示。
- **摘要**：只展示用户需要的版本/状态，不逐行暴露内部记录。
- **不展示**：空兼容表或应用内部实现事实，不构成产品资料。
- **并行域**：由角色、弧盘、怪物或玩法域的窄提供器展示。

## 发行与来源追溯（4 表）

| 表 | 行数 | 是否展示 | 展示入口 / 理由 |
| --- | ---: | --- | --- |
| `dataset` | 1 | 摘要 | 顶部发行信息展示 dataset、schema、importer 和构建时间。 |
| `schema_migration` | 29 | 摘要 | 只展示当前 schema 版本；迁移历史是维护事实。 |
| `source_file` | 8,147 | 展示 | “来源追溯”统一搜索；复制相对路径和 SHA-256。 |
| `source_row` | 47,750 | 展示 | 按 row key、内容哈希或来源路径搜索，并在来源文件内分页。 |

发行 manifest 的 `source_payloads_omitted=true` 时，只展示上述保留元数据；不显示或承诺完整原始 payload。

## 应用内部默认值（1 表）

| 表 | 行数 | 是否展示 | 展示入口 / 理由 |
| --- | ---: | --- | --- |
| `application_setting_default` | 4 | 不展示 | 属于应用运行默认值，不是游戏资料；继续由设置服务消费。 |

## v30 发行注解、正式术语与养成投影（14 表）

| 表 | 行数 | 是否展示 | 展示入口 / 理由 |
| --- | ---: | --- | --- |
| `character_release_evidence` | 5 | 摘要 | 角色品质、获取与大陆服日期证据目录。 |
| `character_release_annotation` | 23 | 合并详情 | 角色品质、获取方式和大陆服日期；保留正式/审阅回退来源类型。 |
| `character_release_evidence_link` | 67 | 不展示 | 角色字段到证据 key 的审计关系。 |
| `localized_term` | 138 | 合并详情 | 正式术语身份、文本表/key 与缺名状态。 |
| `localized_term_name` | 101 | 合并详情 | 当前可用 locale 的玩家可读名称；raw ID 不充当名称。 |
| `character_acquisition_membership` | 23 | 合并详情 | 常驻/限定正式关系及 free 审阅注解。 |
| `fork_lottery_campaign` | 8 | 合并详情 | 限定弧盘、正式卡池标题和发行顺序。 |
| `damage_resistance_term` | 8 | 合并详情 | 抗性身份到正式属性字段的关系；缺正式文本时保持缺名。 |
| `progression_item` | 105 | 合并详情 | 养成材料、货币的稳定 ID、正式名称和本地化 key。 |
| `progression_item_alias` | 1 | 不展示 | `progression_cost` 上下文的 exact token 别名。 |
| `item_quality_term` | 6 | 合并详情 | 品质等级、颜色和正式本地化 key。 |
| `clone_drop_projection` | 148 | 合并详情 | 副本掉落闭包及 complete/partial/unavailable。 |
| `clone_drop_projection_item` | 1,172 | 合并详情 | 只保存确定正整数的单次材料产出。 |
| `clone_drop_projection_gap` | 170 | 不展示 | 概率、分支不一致、缺组或缺名的审计缺口。 |

## 角色与养成（21 表，并行域）

| 表 | 行数 | 是否展示 | 展示入口 / 理由 |
| --- | ---: | --- | --- |
| `character` | 25 | 并行域 | 角色目录与角色详情。 |
| `character_annotation` | 25 | 合并详情 | 角色正式身份旁标记项目注解，不单列内部映射。 |
| `character_awaken_effect` | 184 | 并行域 | 角色 → 觉醒。 |
| `character_awaken_skill_level_bonus` | 66 | 合并详情 | 觉醒效果内展示正式技能等级修改。 |
| `character_likeability_bonus` | 21 | 并行域 | 角色 → 好感度加成。 |
| `character_likeability_bonus_property` | 21 | 合并详情 | 好感度属性明细。 |
| `character_panel_growth` | 1,978 | 并行域 | 角色 → 等级/突破成长，懒加载。 |
| `character_skill` | 92 | 并行域 | 角色 → 技能，并可跳 GA。 |
| `character_skill_level` | 828 | 合并详情 | 技能等级与材料明细。 |
| `character_weight_recommendation` | 23 | 并行域 | 角色 → 推荐权重，并标记项目/外部来源。 |
| `character_weight_recommendation_property` | 151 | 合并详情 | 推荐权重属性明细。 |
| `character_shape_bonus` | 0 | 不展示 | 已空的旧官方角色形状兼容表。 |
| `character_shape_bonus_property` | 0 | 不展示 | 上述空兼容表的子表。 |
| `logical_character_shape_bonus` | 22 | 并行域 | 角色 → 正式额外形状。 |
| `logical_character_shape_bonus_property` | 22 | 合并详情 | 额外形状属性。 |
| `character_cultivation_guide` | 23 | 并行域 | 角色 → 养成指南。 |
| `character_cultivation_attribute_recommendation` | 118 | 合并详情 | 养成指南属性推荐。 |
| `character_cultivation_fork_recommendation` | 46 | 合并详情 | 养成指南弧盘推荐并跳弧盘域。 |
| `character_cultivation_stage` | 184 | 并行域 | 角色养成阶段。 |
| `character_cultivation_stage_skill` | 768 | 合并详情 | 阶段内技能推荐。 |
| `character_graduation_template` | 22 | 展示 | “装备与养成”搜索；明确标记为派生显示值。 |

## 弧盘 / 武器（9 表，并行域）

| 表 | 行数 | 是否展示 | 展示入口 / 理由 |
| --- | ---: | --- | --- |
| `fork_type` | 5 | 并行域 | 弧盘分类。 |
| `fork_item` | 49 | 并行域 | 弧盘目录与详情。 |
| `fork_upgrade_level` | 1,600 | 合并详情 | 弧盘等级强化曲线。 |
| `fork_breakthrough` | 343 | 合并详情 | 弧盘突破阶段。 |
| `fork_modify_pack` | 1,943 | 合并详情 | 弧盘属性修改条件。 |
| `fork_modify_value` | 2,286 | 合并详情 | 弧盘属性修改值。 |
| `fork_refinement_parameter_value` | 975 | 合并详情 | 混频参数逐级值。 |
| `fork_star_level` | 245 | 合并详情 | 混频标题、说明和 Buff 关系。 |
| `fork_star_parameter` | 720 | 合并详情 | 混频参数定义。 |

## 装备、套装与强化（20 表，B 域）

| 表 | 行数 | 是否展示 | 展示入口 / 理由 |
| --- | ---: | --- | --- |
| `equipment_attribute` | 53 | 展示 | “装备与养成”按正式属性 ID/中文名搜索。 |
| `equipment_shape` | 12 | 展示 | 驱动形状详情。 |
| `equipment_shape_cell` | 38 | 合并详情 | 形状格位坐标。 |
| `equipment_suit` | 12 | 展示 | 卡带套装目录。 |
| `equipment_suit_effect` | 24 | 合并详情 | 套装二/四件效果，并跳 Buff 或修改包。 |
| `equipment_suit_required_shape` | 48 | 合并详情 | 套装要求形状并可关系跳转。 |
| `equipment_item` | 74 | 展示 | 空幕/卡带与驱动模板，按类型、品质、套装和资源路径搜索。 |
| `equipment_base_attribute_curve` | 74 | 展示 | 装备主属性强化曲线。 |
| `equipment_base_attribute_point` | 578 | 合并详情 | 单曲线的等级/数值点。 |
| `equipment_strength_level` | 300 | 合并详情 | 单装备强化包的等级消耗。 |
| `equipment_core_random_attribute` | 11 | 合并详情 | 卡带随机主属性的正式说明。 |
| `equipment_modify_pack` | 18 | 展示 | 套装静态属性修改包。 |
| `equipment_modify_value` | 18 | 合并详情 | 修改包逐项属性、运算和值。 |
| `equipment_buff_curve` | 56 | 展示 | 装备 Buff 数值曲线。 |
| `equipment_buff_curve_point` | 56 | 合并详情 | 装备 Buff 曲线点。 |
| `equipment_plan` | 23 | 展示 | 官方角色装备图纸。 |
| `equipment_plan_cell` | 460 | 合并详情 | 图纸 5×5 格位。 |
| `equipment_plan_core_attribute` | 77 | 合并详情 | 图纸卡带主属性。 |
| `equipment_plan_module` | 164 | 合并详情 | 图纸驱动模板并可跳装备。 |
| `equipment_plan_recommended_attribute` | 118 | 合并详情 | 图纸推荐属性并可跳属性目录。 |

## 技能、伤害、Buff、Blueprint 与动画（27 表，B 域）

| 表 | 行数 | 是否展示 | 展示入口 / 理由 |
| --- | ---: | --- | --- |
| `skill_damage` | 907 | 展示 | “技能与伤害”按伤害项/GE ID、GA、类型搜索；详情保留全部六个破坏/冲量字段。GA 与同名 GE 仅在正式目录存在时提供跳转，否则关系状态显示 `unavailable`。 |
| `skill_damage_modifier` | 14 | 合并详情 | 项目倍率修正单独标记为项目注解。 |
| `gameplay_ability_catalog` | 221 | 展示 | GA 正式 ID、中文名和资源路径。 |
| `gameplay_ability_description` | 430 | 合并详情 | GA 说明。 |
| `gameplay_ability_level_hint` | 689 | 合并详情 | GA 等级提示及伤害/防御/治疗 GE 关系。 |
| `gameplay_effect_catalog` | 6,151 | 展示 | “Buff 与效果”统一搜索，固定分页。 |
| `character_combat_ability_binding` | 231 | 合并详情 | 角色域技能关系跳转，不独立暴露 binding 表。 |
| `combat_ability_effect_binding` | 1,753 | 合并详情 | Ability Blueprint 的事件 → GE 关系，分页读取。 |
| `combat_ability_montage_binding` | 1,030 | 合并详情 | Ability Blueprint → Montage，分页读取。 |
| `combat_effect_definition` | 453 | 展示 | 项目结构化效果注解，明确标记非原生运行事实。 |
| `combat_effect_buff_link` | 425 | 合并详情 | 结构化效果 → Buff/GE 关系。 |
| `combat_blueprint_asset` | 8,006 | 展示 | “资源与动画”按名称、类型和资源路径分页搜索。 |
| `combat_blueprint_reference` | 52,977 | 合并详情 | 单 Blueprint 关系分页，不全量装载。 |
| `combat_blueprint_semantic_property` | 9,730 | 合并详情 | 单 Blueprint 白名单语义属性分页。 |
| `combat_blueprint_tag` | 14,190 | 展示 | Gameplay Tag 可统一搜索、复制并跳来源 Blueprint；详情分页。 |
| `combat_montage` | 1,116 | 展示 | Montage 资源目录。 |
| `combat_montage_section` | 1,445 | 合并详情 | 单 Montage Section。 |
| `combat_montage_notify` | 23,819 | 合并详情 | 单 Montage Notify 分页读取。 |
| `buff_definition` | 3,058 | 展示 | Buff key、资源路径、持续/周期/叠层策略。 |
| `buff_modifier` | 565 | 合并详情 | 属性、运算、magnitude、Calculation 和 Tag 条件。 |
| `buff_trigger_effect` | 1,276 | 合并详情 | 触发事件、目标效果、叠层和持续修改。 |
| `combat_curve` | 798 | 展示 | 战斗效果曲线，复合正式 key 后读取曲线点。 |
| `combat_curve_point` | 1,743 | 合并详情 | 单战斗曲线点。 |
| `combat_level_curve` | 6 | 展示 | 等级伤害/反应曲线。 |
| `combat_level_curve_point` | 160 | 合并详情 | 单等级曲线点。 |
| `reaction_definition` | 6 | 展示 | 正式反应组合及默认伤害项关系。 |
| `combat_effect_constant` | 39 | 展示 | 战斗公式常量及单位/说明。 |

## 怪物、玩法与敌方公式（28 表，并行域）

| 表 | 行数 | 是否展示 | 展示入口 / 理由 |
| --- | ---: | --- | --- |
| `enemy_combat_profile` | 4,000 | 并行域 | 敌方等级公式画像，按目标详情懒加载。 |
| `enemy_element_resistance` | 32,000 | 合并详情 | 单画像的元素抗性，不做独立大列表。 |
| `roguelike_modifier_profile` | 181 | 展示 | “Buff 与效果”可按属性包 ID、条件或属性 ID 搜索；详情结构化显示条件，不按名称猜 owner。 |
| `roguelike_modifier_property` | 1,061 | 合并详情 | 属性包逐项展示 `property_id`、operation、value 与 `sort_key`。 |
| `monster_instance_profile` | 4,311 | 并行域 | 怪物实例画像，统一搜索和分页。 |
| `monster_instance_profile_variant` | 5,481 | 合并详情 | 同实例的属性包变体。 |
| `abyss_level` | 152 | 并行域 | 轨外层/半场配置。 |
| `abyss_level_monster_spawn` | 500 | 合并详情 | 轨外刷怪槽位。 |
| `abyss_monster_pool_entry` | 648 | 合并详情 | 怪物池成员。 |
| `monster_catalog` | 35 | 并行域 | 官方图鉴。 |
| `monster_identifier_alias` | 319 | 合并详情 | 正式 ID/类路径别名证据。 |
| `monster_template_binding` | 85 | 合并详情 | 官方图鉴对象 ↔ 正式怪物模板的显式身份绑定，不按画像相似推断。 |
| `monster_boss_support` | 55 | 合并详情 | Boss 分类项目注解。 |
| `outer_realm_rotation` | 6 | 并行域 | 当前/下一轨外轮换。 |
| `outer_realm_season_buff` | 2 | 合并详情 | 赛季 Buff。 |
| `outer_realm_season_buff_component` | 4 | 合并详情 | 赛季 Buff 结构化分量。 |
| `feast_stage` | 8 | 并行域 | 争锋赏宴对象。 |
| `feast_stage_difficulty` | 32 | 合并详情 | 争锋难度画像。 |
| `feast_option` | 54 | 并行域 | 争锋加成选项目录。 |
| `feast_stage_option` | 144 | 合并详情 | 对象可选加成关系。 |
| `divination_buff` | 7 | 并行域 | 魔女赐福。 |
| `clone_activity_category` | 7 | 并行域 | 材料/养成副本分类。 |
| `clone_activity` | 56 | 并行域 | 副本目录。 |
| `clone_activity_difficulty` | 218 | 合并详情 | 副本难度。 |
| `clone_spawn_member` | 80 | 合并详情 | 副本刷怪成员。 |
| `high_risk_commission` | 13 | 并行域 | 高危委托目录。 |
| `high_risk_commission_difficulty` | 78 | 合并详情 | 高危逐难度怪物池。 |
| `high_risk_monster_pool_member` | 55 | 合并详情 | 高危怪物池成员。 |

## 性能与安全边界

- 全域或分域搜索只接受固定领域 key；SQL 表、字段和排序均由 DAO 白名单固定，用户输入只作为参数值。
- 搜索词最长 200 个字符；每页 1–100 条，默认 50 条。UI 应在用户输入后防抖调用，不预建数千个控件。
- 3k–8k 级实体表只返回当前页；47,750 条来源行只在来源搜索或来源文件内分页。
- 52,977 条 Blueprint 引用、14,190 条 Tag、9,730 条语义属性和 23,819 条 Notify 只按单个资源 key
  分页，不随搜索结果或基本详情全量加载。
- 关系跳转只使用正式 ID、GA/GE/Buff key、Gameplay Tag 或资源路径；不存在的目标保持未解析，不按中文名猜。
- DAO 复用 `StaticGameDataDao` 的 SQLite `mode=ro` 与 schema v30 校验；没有写入方法，也不接受任意 SQL。

## 集成接线

集成任务创建 `StaticCatalogMiscService(static_database_path, manifest_path=data/manifest.json)`，将页面搜索框的
全库搜索绑定到 `search("all", ...)`，分类搜索绑定到五个领域 key。选择搜索项后调用 `detail(kind, key)`；
装备/技能/效果/资源 DTO 交给 `EquipmentEffectDetail.render()`，来源 DTO 交给
`SourceTraceDetail.render_trace()`。Blueprint/Notify 关系用 `asset_relations()` 分页，来源行用
`source_rows()` 分页。公共详情只携带关系种类与总数，页面在用户点击后才经 Controller 加载当前 50 行；
`target_available=false` 行保留明确的不可用证据且没有跳转按钮。可用关系按钮发出的
`(target_kind, target_key)` 回到页面 Controller，由 Controller 选择本域 Service 或并行角色/弧盘/怪物域
Service；详情组件不定位页面、DAO 或 MainWindow。
