# 发行版静态游戏数据库

`game_static.sqlite3` 是应用随安装包分发的只读基础数据库。它由开发者从
本机准备好的游戏官方文件生成，普通用户不需要另外下载。

当前数据集、结构版本、生成时间、SHA-256 和原始 payload 省略状态统一读取
`manifest.json`，本说明不再手工复制这些容易过期的值。

2026-08-19 官方文件更新涉及角色、培养指南、弧盘、技能、GameplayEffect、怪物、深渊和装备效果数据。
发行库包含 22 条实时工坊权重和 1 条确认覆盖；灵可 `1072` 的工坊接口缺行，使用
`tools/game_data/character_weight_overrides.json` 中的确认权重，不回落到通用四项默认。

发行数据库保留规范化业务表、来源文件相对路径、来源文件哈希、来源行键和
来源行内容哈希。`source_row.payload_json` 必须全部为 `NULL`，完整来源原文和
构建报告只保存在开发者工作区，不进入项目仓库。

重新生成时使用：

```powershell
$localConfig = if ($env:NTE_LOCAL_CONFIG) { Get-Content -Raw -LiteralPath $env:NTE_LOCAL_CONFIG | ConvertFrom-Json } else { $null }
$gameDataSource = if ($localConfig) { $localConfig.official_content_root } else { "../Content" }
$gameDataWorkspace = if ($localConfig) { $localConfig.game_data_workspace } else { "build" }
$gameDataSetId = if ($localConfig) { $localConfig.dataset_id } else { "game-version_and_date" }
$gameDataAsOf = if ($localConfig) { $localConfig.as_of } else { "YYYY-MM-DD" }

python tools/game_data/build_static_database.py `
  --source $gameDataSource `
  --output "data\game_static.sqlite3" `
  --report-dir "$gameDataWorkspace\reports\distribution_database" `
  --dataset-id $gameDataSetId `
  --as-of $gameDataAsOf `
  --manifest "data\manifest.json" `
  --omit-source-payloads
```

构建器会自动导入 `DataTable/Character/Awaken/*AwakenEffect*.json` 中的
角色六觉、三/六觉共鸣和其中明确给出的技能等级加成。生成后必须运行静态数据库测试，
并重新生成 `manifest.json`。发布前检查会核对清单与实际数据库，不一致时拒绝继续。

如果下一版本需要继续从某个已发布版本迁移历史可编辑的额外形状值，还要从该版本
确认未修改的发行库生成基线：

```powershell
python tools/game_data/export_shape_bonus_baseline.py `
  --database "data\game_static.sqlite3" `
  --output "data\migrations\shape_bonus_defaults_<version>.json" `
  --release-version "<version>"
```

基线只包含过去允许编辑的两个逻辑形状表，不包含完整官方数据库内容。

同时会依据 `DT_Character.ElementData.PropModifyID` 关联
`DT_PlayerPackData.json` 与 `DT_PlayerModifyPackData.json`，生成角色 1–80 级、
六段突破前后的官方基础生命、攻击和防御。关联按官方代号不区分大小写，不依赖中文名。

`DT_CharacterAbilityConfig.json` 与 `DT_CharacterAbilityEffectConfig.json` 还会生成
角色技能目录、主动/被动类型、技能标签、升级所需突破/觉醒等级和材料。尚未有官方技能配置
的角色会保留在角色目录中，但不会生成虚构技能。

`DataTable/skill/DT_SkillDamageData.json` 与
`DT_SkillDamageGameplayModifyData.json` 会生成官方伤害执行参数和修正系数，并按官方
`GAName` 关联至角色技能。该库只保存原始倍率数组、属性和破坏参数，不在生成阶段或 DAO
中推导直接伤害。

schema v8–v10 还保存倾陷/环合等级曲线、环合常量、普通与 999 夜敌方属性包、
怪物实例等级变体，以及 Abyss 关卡到波次、怪物池和普通属性包的明确关系。
`FT_` 属于 999 夜子玩法，不作为 Abyss 或轨外之境的场景判断依据。

schema v11 保存开发期从异环工坊 API 同步的角色推荐权重，并保留 `workshop_api`、
`workshop_cache` 或 `default` 来源标记。用户运行时会把推荐复制到账号库后独立编辑；
发行应用不访问该 API，也不读取旧角色 JSON 权重。

schema v12 起为全部具备完整战斗目录的可用角色保存构建期固定的直伤毕业模板。模板沿用旧页面的
20 格满驱动、四条最高权重满词条、满级精 1 专属弧盘、固定图纸额外形状数量和
伤害最优空幕主词条规则；运行时直接读取模板及基准伤害，不再读取 `stats.json`、
调用图纸求解器或重复搜索空幕主词条。角色均使用官方默认套装、有效推荐弧盘和当前静态推荐权重生成
`official_default` 模板。

schema v13–v16 保存设置默认、官方额外形状基线、逻辑角色形状和逐级弧盘精炼参数。schema v17 保存
培养指南中的推荐弧盘、属性、阶段与技能等级，并新增技能说明、GameplayEffect 索引、怪物手册别名、
装备 Modify/曲线和统一效果定义。弧盘的星级包 ID 按不区分大小写的官方主键规范化，构建时要求精炼等级
和说明数量与 `max_star` 完整一致。
