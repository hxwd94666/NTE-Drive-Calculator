# 游戏资料库：怪物与玩法域

本域只读发行静态库 `data/game_static.sqlite3`，不读取账号库或战报，不修改静态数据，不按
中文名猜测怪物 ID。“工具 → 游戏资料库”组合根已通过公开
`build_monster_catalog_page(...)` 工厂注入 `StaticCatalogMonsterService`、公共术语服务与游戏 UI
资源目录，构建独立 `MonsterCatalogPage`。页面公开 `open_record(record_id)` 供资料库容器打开正式记录；
成功时返回 `True`，记录缺失时返回 `False`，以便资料库容器回滚跨域导航。页面内部的玩法、期数、层级、
怪物与详情返回由自身导航层级管理。

## 数据边界

| 展示域 | schema v30 事实源 | 当前发行库覆盖 |
| --- | --- | ---: |
| 官方图鉴 / 大世界 | `monster_catalog`、`monster_identifier_alias` | 35 个图鉴对象 |
| 怪物模板与等级画像 | `monster_instance_profile`、`monster_instance_profile_variant` | 4,311 个模板画像 |
| 图鉴与模板身份绑定 | `monster_template_binding` | 85 条显式绑定 |
| 异象追猎单 Boss | `monster_catalog.enemy_type='WeeklyBoss'` 与 `world_boss_id` | 7 个 Boss |
| 争锋赏宴 | `feast_stage`、`feast_stage_difficulty`、`feast_*option` | 8 期挑战对象、32 个难度 |
| 轨外之境 | `outer_realm_rotation`、`abyss_level*`、`abyss_monster_pool_entry` | 6 个有时间的配置，含层/上下半场/怪物池 |
| 材料与养成副本 | `clone_activity*`、`clone_spawn_member` | 56 个活动、218 个难度；159 条展示于冒险手册、59 条隐藏，68 条没有类目 ID |
| 高危委托 | `high_risk_commission*`、`high_risk_monster_pool_member` | 13 个委托、78 个难度 |
| 数值画像 | `enemy_combat_profile`、`enemy_element_resistance` | 生命、防御、倾陷、8 类抗性 |
| 来源追溯 | `source_row` → `source_file` | 相对路径、行键、内容/文件 SHA-256 |

发行 manifest 标记 `source_payloads_omitted=true`；来源键与摘要只用于底层导入审计，不进入玩家主界面，
也不声称可查看完整原始 payload。当前 schema 没有怪物攻击属性/攻击档字段；界面显示“不可用”，不从等级、中文说明
或战报残差反推。高危委托只有通用回退池而没有逐难度怪物池时，同样明确标记不可用。

## 事实类型

- **官方静态事实**：正式图鉴 ID、模板 ID、类路径、玩法配置、难度、刷怪槽位和来源键。
- **等价公式画像**：`profile_set + pack_id` 连接的生命、防御、倾陷和抗性。多个身份可以共用
  同一画像，共用画像不证明怪物身份。
- **项目派生 / 注解**：“当前期 / 下一期”、字段不可用原因及展示标签。它们不回写静态库。

身份与玩法关系只使用 `monster_template_binding`、玩法表中的正式模板 ID，或正式 Unreal 类路径的对象名，
并由当前玩法页直接渲染为所属期数、层、半场、刷怪槽位和画像摘要；不把普通钩稽关系做成跨页跳转。
Buff 与玩法规则同样在当前页完整展示名称、说明、数值与生效条件，不提供机制跳转按钮。底层正式机制 ID
仅保留作导入与数据一致性审计。争锋赏宴内嵌七项正式魔女赐福选择及效果摘要，但不把赐福并入敌方
挑战条件或画像。任何关系都不使用中文名、数值画像相似或战报推断来建立怪物身份。

## 当前期与下一期口径

查询时把上海时区的当前时刻与 `starts_at_mainland` / `ends_at_mainland` 比较：

1. 时刻在开始和结束之间（含边界）的配置是“当前期”。
2. 当前时刻之后开始时间最早的配置是“下一期”。
3. 其他配置标记为“已结束”或“待开放”。

`inference_ordinal` 作为官方导入字段展示，不代替大陆服生效时间判定。
