# 发行版静态游戏数据库

`game_static.sqlite3` 是应用随安装包分发的只读基础数据库。它由开发者从
本机准备好的游戏官方文件生成，普通用户不需要另外下载。

当前数据集：`unversioned_20260723_update`
当前结构版本：`12`
当前 SHA-256：`DB27E25AD1D6FACD308980DCD154847C076896A62416BCFD8B89F8CB70C3CE47`

2026-07-23 官方文件更新涉及角色、伊洛伊觉醒、弧盘、怪物、深渊和技能伤害数据。
发行库继续保留 19 条工坊缓存权重和 2 条默认权重；伊洛伊 `1075` 当前仍使用默认权重，
待配置 Open API Key 后由同步脚本在线刷新。

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
  --omit-source-payloads
```

构建器会自动导入 `DataTable/Character/Awaken/*AwakenEffect*.json` 中的
角色六觉、三/六觉共鸣和其中明确给出的技能等级加成。生成后必须运行静态数据库测试，
并同步更新本文件中的数据集和 SHA-256。

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

schema v12 为全部 19 个可用角色保存构建期固定的直伤毕业模板。模板沿用旧页面的
20 格满驱动、四条最高权重满词条、满级精 1 专属弧盘、固定图纸额外形状数量和
伤害最优空幕主词条规则；运行时直接读取模板及基准伤害，不再读取 `stats.json`、
调用图纸求解器或重复搜索空幕主词条。伊洛伊缺少旧角色配置，使用官方默认套装和
静态推荐权重生成 `official_default` 模板。
