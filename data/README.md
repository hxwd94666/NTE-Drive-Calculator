# 发行版静态游戏数据库

`game_static.sqlite3` 是应用随安装包分发的只读基础数据库。它由开发者从
本机准备好的游戏官方文件生成，普通用户不需要另外下载。

当前数据集、结构版本、生成时间、SHA-256 和原始 payload 省略状态统一读取
`manifest.json`，本说明不再手工复制这些容易过期的值。

官方文件更新涉及角色、培养指南、弧盘、技能、GameplayEffect、怪物、深渊和装备效果数据。灵可 `1072`
尚未实装且工坊接口缺行时，使用 `tools/game_data/character_weight_overrides.json` 中的临时发行回退，不回落
到通用四项默认；工坊接口以后返回该角色时，以工坊权重覆盖这份临时回退。发行前必须核对实际库中的
`source_kind`，文档不手工声明容易过期的实时工坊行数。

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
覆盖正式输出前，构建器自动将旧库原子备份到 `build/previous/data/game_static.sqlite3`。打包时有工坊 API
Key 就同步 API；没有 Key 就从该备份继承带来源的旧权重。没有 Key 和备份时发布失败，不允许把整库
`default` 权重直接打入正式包；全 `default` 新库也不能覆盖已有的有效发行备份。

schema v18 还按 `SoftActorClass` 的玩家资产编号关联正式角色，再把
`DT_LikeabilityRoleData.json` 的 10 级映射与 `DT_LikeabilityModifyData.json` 的正式属性修改标准化为角色
好感度加成；不能把好感度表行键当作角色 ID。玩家角色零没有好感度记录。运行时只保存账号是否启用该
加成，不复制或猜测属性数值。

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

schema v13–v16 保存设置默认、逻辑角色形状和逐级弧盘精炼参数。额外形状按
`DT_Character.ElementData.EquipmentSlotID` → `DT_CharacterEquipmentSlotsData.ModifyPropID` →
`DT_EquipmentModifySlotsEffect.ModifyData` 的官方关系直接导入；数值语义是每件匹配格数的驱动提供一次
加成，不按驱动占用格子数倍增。schema v17 保存
培养指南中的推荐弧盘、属性、阶段与技能等级，并新增技能说明、GameplayEffect 索引、怪物手册别名、
装备 Modify/曲线和统一效果定义；schema v18 保存角色好感度 10 级正式属性加成。弧盘的星级包 ID 按不区分大小写的官方主键规范化，构建时要求精炼等级
和说明数量与 `max_star` 完整一致。

schema v19–v21 保存角色战斗 Blueprint 时间证据、规范化 Buff/GE 和修正作用域；schema v22 保存争锋赏宴
对象、难度、加成与魔女赐福；schema v23 保存官方怪物图鉴、材料/养成副本类目、难度、波次刷怪模板及
怪物模板绑定；schema v24 从限时战斗奖励任务保存轨外配置的大陆服生效区间，并在构建日按日切后的状态
标记当前与下一配置；schema v25 为怪物模板、实例和等级变体的不区分大小写查询补充表达式联合索引；
schema v26 保存当前/下一轨外配置的赛季 Buff 元数据、正式曲线值和已审计触发组成。
schema v27 保存轨外怪物池条目的官方本地化名称，供半场目标识别与战报展示直接使用。
`AbyssID` 是整套配置 ID，不能把末尾数字当层数；`AbyssLevel` 才是任务要求的层数。
副本模板没有唯一属性包绑定时只用于目标身份选择，不生成防御或抗性默认值。
