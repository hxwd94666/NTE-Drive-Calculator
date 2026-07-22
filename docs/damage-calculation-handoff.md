# 伤害计算与战斗数据交接

最后更新：2026-07-22。当前分支：`2.0.0`。

## 已完成

- 伤害规则文档：[damage-calculation.md](damage-calculation.md)。用户口述规则为项目金标准；官方静态文件仅补齐可追溯数值。
- 纯计算服务：[damage_calculation_service.py](../src/services/damage_calculation_service.py)。已实现直伤、DOT、倾陷、各可复用乘区、环合归属、技能/环合等级档位工具、覆纹与浸染辅助计算、失谐默认扣除、刷新状态和黯星独立实例状态。
- 静态库升级至 schema v5，发行库为 `data/game_static.sqlite3`。
- 新增怪物实例绑定表：`monster_instance_profile`、`monster_instance_profile_variant`；已导入 730 个静态怪物实例及 5177 条等级变体。
- 已导入 Abyss 关卡绑定链：128 个关卡、429 条波次配置、384 条怪物池条目；Abyss 的 366 个唯一属性包 ID 均来自普通 `DT_MonsterPackData`，与 999 夜的 `DT_MonsterPackData_FT` 无关。
- 完整测试已通过：`python -m unittest discover -s tests`，563 项；运行前如环境变量指向旧 v3 库，请清空 `NTE_GAME_STATIC_DB`。

## 当前项目规则

### 技能 15 档倍率

- 来源为 `DT_SkillDamageData` 的 `AtkRateBaseArray`、`HPRateBaseArray`、`DefRateBaseArray`。
- 档位规则：`有效技能等级 - 1`，超过数组范围钳制到最后一档。
- 当前确认：觉醒等级达到 3 时，全技能等级 `+1`；因此基础 10 级、三重觉醒时取第 11 档（下标 10）。
- 未确认：其他觉醒效果、特殊技能是否存在不同有效等级规则。不要把觉醒等级直接加到技能等级。

### 环合 16 档

- 已确认采用每 5 级一档：`(角色等级 - 1) // 5`。
- 1–5 级为第 1 档，76–80 级为第 16 档。
- 适用创生、浊燃、黯星的等级乘区；数组末档分别为 9000、2700、45000。

### 状态与失谐默认值

- 除黯星外，重复触发默认刷新持续时间而不创建新实例，标记为“未实测”。
- 黯星：不同触发者独立计时、独立爆炸；同一触发者刷新自己的实例。
- 覆纹：记录实际造成伤害，到期追加 `实际伤害 × 20% × 环合系数`。
- 状态提前移除不结算，只有明确的技能专属效果才可提前结算。
- 失谐：暂按 `敌方倾陷上限 × 15%` 扣除。配置中的固定值、等级系数、联机修正尚未解释，不能擅自合入。

## Content 数据来源

Content 根目录：

`C:\softwares\codes\utils\异环\UnrealExporter\output_combat\HT\Content`

| 数据 | Content 相对路径 | 当前用途 |
| --- | --- | --- |
| 技能倍率 | `DataTable/skill/DT_SkillDamageData.json` | 15 档攻击/生命/防御倍率、固定暴击率、倾陷值等 |
| 倾陷等级曲线 | `DataTable/skill/GlobalCharacterData/DT_GlobalCommonData.json` | `UnbaldamagePara`，1–80 级，80级为3603 |
| 环合伤害曲线 | `DataTable/Reaction/DT_ReactionDamageData.json` | 创生、浊燃、黯星16档数组 |
| 环合定义 | `DataTable/Reaction/DT_ReactionData.json` | 元素组合与默认伤害GE |
| 环合常量 | `DataTable/Reaction/DT_ReactionEffectFigure.json` | 时长、周期、覆纹基础比例等 |
| 普通属性包 | `DataTable/PackData/DT_MonsterPackData.json` | 防御、抗性、倾陷上限；Abyss 的 `AttributeID` 实际引用此表 |
| 999 夜属性包 | `DataTable/PackData/DT_MonsterPackData_FT.json` | 999 夜子玩法属性包；`FT_` 前缀不表示轨外之境 |
| 怪物静态表 | `DataTable/Monster/DT_MonsterStaticData_*.json` | 实例ID、默认 `PropModifyID`、世界/副本/深渊等级变体 |
| Abyss 怪物池 | `DataAssets/DataAssetSet/Abyss/DT_AbyssMonsterPool.json` | 怪物类、怪物等级、`AttributeID` 属性包ID |
| Abyss 关卡 | `DataAssets/DataAssetSet/Abyss/AbyssCloneLevelDataTable.json` | 关卡到怪物池 `MonsterPoolID` 的关联 |

`FT_` 前缀表示 999 夜子玩法内容，不表示轨外之境，也不能单独判定 Abyss 场景。999 夜不是当前伤害模拟的重点；Abyss 场景关系只以 `DataAssets/DataAssetSet/Abyss` 的专用配置链为准。

## 下一位 AI 最值得完成的工作

1. 若继续完善伤害模拟，优先处理环合状态机的实测边界：刷新、到期、重复触发、DOT周期与服务端事件顺序。
2. 继续研究失谐配置的原生执行逻辑；在拿到公式前维持15%默认实现并保留待定标记。

## 关键代码与验证

- 构建器：[build_static_database.py](../tools/game_data/build_static_database.py)，所有 Content 相对路径集中在 `TABLE_PATHS`。
- DAO：[static_game_data_dao.py](../src/storage/sqlite/static_game_data_dao.py)。
- 迁移：[004_game_static_monster_binding.sql](../src/storage/sqlite/schema/004_game_static_monster_binding.sql)。
- 伤害单测：[test_damage_calculation_service.py](../tests/test_damage_calculation_service.py)。

重建发行 SQLite 的命令示例：

```powershell
python tools/game_data/build_static_database.py `
  --source "C:\softwares\codes\utils\异环\UnrealExporter\output_combat\HT\Content" `
  --output "data\game_static.sqlite3" `
  --report-dir "C:\Temp\nte-drive-calculator-static-db-report" `
  --dataset-id "unversioned_20260722_combat" `
  --as-of 2026-07-22 `
  --omit-source-payloads
```

完成数据改动后至少运行：

```powershell
$env:NTE_GAME_STATIC_DB=''
python -m unittest discover -s tests
git diff --check
```
