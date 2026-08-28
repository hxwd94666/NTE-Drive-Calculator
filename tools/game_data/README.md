# 游戏静态数据开发工具

这里仅保存可公开审查的数据整理、校验代码和开发者注释，不保存游戏官方文件、由其整理出的原始 JSON、图片或生成的数据库。

## 数据原则

- 游戏记录、ID、名称和关系只来自开发者本机的游戏官方数据文件；角色推荐权重是单独标注来源的工坊 API 开发期快照，不冒充游戏官方字段。
- 标准化查询表以原始游戏 ID 为主键，同时在 `source_row.payload_json` 保留完整来源行。
- 人工确认的特殊角色分类单独进入 `character_annotation`，不能修改原始角色记录。
- 新数据库和新运行链路不读取旧 `roles.json`、`sets.json`、`shapes.json`、`tapes.json` 或 `weapons.json`。

## 角色数据源清单

开发者本机的绝对路径、数据集编号和日期统一保存在仓库外的 JSON 配置中。
设置 `NTE_LOCAL_CONFIG` 后可直接读取；未设置时仍可使用公开的相对路径示例：

```powershell
$localConfig = if ($env:NTE_LOCAL_CONFIG) { Get-Content -Raw -LiteralPath $env:NTE_LOCAL_CONFIG | ConvertFrom-Json } else { $null }
$gameDataSource = if ($localConfig) { $localConfig.official_content_root } else { "../Content" }
$gameDataWorkspace = if ($localConfig) { $localConfig.game_data_workspace } else { "." }
$gameDataSetId = if ($localConfig) { $localConfig.dataset_id } else { "release_YYYYMMDD" }
$gameDataAsOf = if ($localConfig) { $localConfig.as_of } else { "YYYY-MM-DD" }

python tools/game_data/catalog_characters.py `
  --source $gameDataSource `
  --output-dir "$gameDataWorkspace\reports\characters" `
  --as-of $gameDataAsOf
```

分类规则位于 `character_overrides.json`。它只补充特殊形态和玩法配置的分类，不提供游戏名称，也不决定角色是否存在。

## 构建静态 SQLite v29

```powershell
python tools/game_data/build_static_database.py `
  --source $gameDataSource `
  --output "$gameDataWorkspace\build\game_static.sqlite3" `
  --report-dir "$gameDataWorkspace\reports\static_database" `
  --dataset-id $gameDataSetId `
  --as-of $gameDataAsOf
```

提交到项目并随安装包分发的数据库必须省略来源行原文：

```powershell
python tools/game_data/build_static_database.py `
  --source $gameDataSource `
  --output "data\game_static.sqlite3" `
  --report-dir "$gameDataWorkspace\reports\distribution_database" `
  --dataset-id $gameDataSetId `
  --as-of $gameDataAsOf `
  --manifest "data\manifest.json" `
  --omit-source-payloads
```

发行数据库仍保留来源文件相对路径、文件哈希、来源行键和内容哈希，但
`source_row.payload_json` 为 `NULL`。完整来源内容只保留在开发者工作区。

战斗 Blueprint 导入同时规范化 `{TagName: ...}` 与 UE5
`InheritableAssetTags`/`InheritableGameplayEffectTags` 中的字符串 GameplayTag；DOT 等正式身份通过
`combat_blueprint_tag` 查询。`HTExtractAttributeGEComp` 的属性抽取类型及比例曲线引用作为语义字段保留，
供生命转移机制审计；`UseSourceObject` 同样作为通用施加者语义保留，不从说明文本猜测。

完整审计数据库放在项目外；省略来源行原文的发行数据库放在
`data/game_static.sqlite3`。每次游戏版本更新时，开发者从本机游戏官方数据文件重新整理数据库，检查来源哈希、数量和外键后再更新发行数据库。游戏官方文件和中间数据不进入开源仓库。

更新正式的 `data/game_static.sqlite3` 时，构建器会先把旧发行库原子备份到
`build/previous/data/game_static.sqlite3`，再生成并替换新库。该备份是无 Key 时继承既有工坊默认权重的唯一
输入，不得在新库覆盖旧库后再从账号库、共享库或旧 `roles.json` 补值。自定义输出路径必须显式传入
`--backup-existing-to`；正式发行路径不需要手工复制。只有含 `workshop_api`/`workshop_cache` 权重的有效
发行库可以刷新备份；尚未同步的全 `default` 新库不得覆盖已有备份。

新库生成后，打包入口按以下二选一门禁更新角色推荐权重：

1. 存在异环工坊 Open API Key 时，必须用 API 原子同步：

```powershell
python tools/game_data/sync_recommended_weights.py `
  --database data/game_static.sqlite3 `
  --config-dir config
```

API 没有返回的角色保留本次构建产生的角色级发行回退；`character_weight_overrides.json` 仅保存角色尚未
实装、工坊暂时缺行期间的临时回退。工坊 API 一旦返回该角色，API 权重优先并替换临时回退。Key 只从开发机
隐藏输入、环境或 `.env` 读取，不写入数据库或安装包；
应用运行时只读静态库，不访问 API，也不读取旧 `roles.json` 权重。

2. 没有 API Key 时，打包入口自动从构建前备份的发行库继承带
   `workshop_api`/`workshop_cache` 来源的行。也可以单独执行：

```powershell
python tools/game_data/sync_recommended_weights.py `
  --database data/game_static.sqlite3 `
  --reuse-database build/previous/data/game_static.sqlite3 `
  --manifest data/manifest.json
```

继承时，旧库已有角色继续使用旧工坊权重，只有旧库不存在的新角色保留新库的角色级发行回退。API 同步和
旧库继承都会按最终权重重新生成全部角色毕业模板，保证推荐词条与毕业基准来自同一版静态快照。如果既没有
可用 Key，也没有构建前备份，发布必须失败；不得跳过同步并把整库 `default` 权重作为正式发行产物。
正式发布入口必须自动执行同一套“备份 → 新建 → API 同步或旧库继承”流程；`--skip-workshop-sync` 只允许
开发期诊断，不得用于生成正式发行包。

角色额外形状不从上一发行库继承。构建器直接关联官方
`DT_Character.ElementData.EquipmentSlotID`、`DT_CharacterEquipmentSlotsData.ModifyPropID` 与
`DT_EquipmentModifySlotsEffect.ModifyData`，按逻辑角色写入形状格数和每件匹配驱动提供的属性值。角色变体
必须得到相同规则；关系缺失、属性未知、存在条件或不是加法修正时，构建直接失败。运行时官方角色只读这两
张静态表，基础权重页和旧 `app_shared.sqlite3` 覆盖均不能修改有效值；只有账号自创角色使用账号私有配置。

构建器还会自动扫描 `DataTable/Character/Awaken/*AwakenEffect*.json`：每个角色的六个
可选觉醒、三/六觉共鸣、名称/描述/图标、Buff 引用和明确的技能等级加成都会进入静态库。
用户拥有的副本数和实际激活的觉醒属于账号私有计算配置，不写入发行静态库。

角色战斗曲线除 `*EffectFigure.json` 外，还纳入当前噩梦重放所需的
`DT_GlobalValueLacrimosaData.json`。E 与极轨终结的附加层数分别读取
`Lacrimosa_Skilldotnum_1`、`Lacrimosa_UltraSkilldotnum_1`，运行时不得再用硬编码替代官方曲线。

`DT_LikeabilityRoleData.json` 中每个角色的 10 级映射会先以 `SoftActorClass` 的玩家资产编号关联正式角色，
不能假定该表的行键就是 `character_id`；随后再关联
`DT_LikeabilityModifyData.json`，只接受无条件、加法且属于正式属性目录的修改项，写入 schema v18
好感度表。玩家角色零没有好感度记录，不补造十级加成；角色页只保存账号是否启用，不把静态属性值复制进
账号库。

角色基础成长由 `DT_Character.ElementData.PropModifyID` 关联
`DT_PlayerPackData.json` 的 `*_base` 行与 `DT_PlayerModifyPackData.json` 的
`*_lv_1..80`、`*_stage_1..6` 累计修改行生成。构建器为每位角色输出 86 条有效状态：
普通等级、六个突破等级各一条突破前/后状态，以及满级状态。

角色技能目录来自 `DT_CharacterAbilityConfig.json`，每个技能记录官方技能 ID、类型、顺序、
显示标记和所有等级的突破/觉醒要求及材料。若
`DT_CharacterAbilityEffectConfig.json` 有对应记录，还会写入技能标签和 Gameplay Effect 资源路径；
未配置技能表的角色不会阻断整库构建。

`LevelsCostItems` 的 1–9 行表示九次升级要求，运行时技能基础等级范围为 1–10。三觉共鸣中正式
`SkillLevel` 修改可把生效等级提升到 11；六觉没有该修改。Ability 中文名来自培养/技能说明目录，应用只在
展示 service 中解析，GA/GE 稳定标识继续用于存储、计算和战报。

`DataTable/skill/DT_SkillDamageData.json` 的伤害执行记录和
`DT_SkillDamageGameplayModifyData.json` 的攻击倍率修正也会写入静态库。它们只按
`GAName` 关联既有技能，保留等级数组、元素和破坏参数；构建器与 DAO 均不计算直接伤害。
其中 `FTAtkRateBaseCoefficient` 作为资产元数据保留，不参与生命伤害的基础倍率；逐击和角色页直接读取
`AtkRateBaseArray`，只有已审计的觉醒或被动适配器可以再修改基础倍率。

`character_overrides.json` 中标记为 `combat_transformation` 的记录只保留官方角色目录和
规范角色关联；它们共用规范角色的属性与养成，不能生成独立的成长、觉醒或普通技能目录。

schema v8–v10 新增倾陷/环合曲线、敌方属性包、怪物实例等级变体和 Abyss 关卡绑定；schema v11 新增带来源标记的角色推荐权重；schema v12 新增构建期固定的逐角色直伤毕业模板；schema v13–v16 新增设置默认、额外形状和弧盘精炼参数；schema v17 新增培养指南、技能说明、GameplayEffect 索引、怪物别名和装备效果来源；schema v18 新增角色好感度 10 级属性加成。
schema v19 从同一 `Content` 根的精确角色战斗 Blueprint 范围导入角色输入绑定、Ability→Montage、
事件→GameplayEffect、对象引用、GameplayTag、关键效果属性以及 Montage Section/Notify 时间；不扫描
地图、音频或未列入导出配置的表现资源。推断服务消费这些静态证据，不把推算结果写回静态事实。
schema v20 补齐普通与 999 夜敌方属性包的生命基础值、比例和固定加值，导入 RogueLike 怪物实例及属性
修正，并从既有 `combat_blueprint_*` 证据规范化 Buff/GameplayEffect 的持续、周期、叠层、属性修正、
事件触发关系，以及装备套装、弧盘精炼、觉醒到运行时效果的绑定。构建器同时扫描全部
`DataTable/Skill/GlobalCharacterData/*EffectFigure.json`，与空幕、弧盘曲线统一写入带曲线表资产路径的
`combat_curve`，避免不同表同名行冲突，并让技能 Buff 不把 `ScalableFloat.Value` 系数误当最终值。
schema v21 为规范化 Buff 增加修正作用域与标签要求；schema v22 从 `BossDIY` 和 `Divination` 官方数据导入
争锋赏宴 8 个对象、4 档难度、54 个挑战加成，以及 7 个魔女赐福及其正式曲线值，供战报目标选择与历史
重放使用。schema v23 从 `DT_MonsterManualConfig`、`DT_MonsterTags`、`DT_CloneOverviewRow`、
`CloneSystemDataTable` 和 `DT_CloneMonsterConfig` 导入官方大世界图鉴身份、7 个活动类目、56 个活动、218 个
难度节点、80 个波次刷怪成员及模板绑定；中文显示优先通过 Text StringTable 的 `TableId + Key` 解析。
正式目录隐藏 ID 明确含 `_test` 的开发活动，但 `source_row` 仍保留其来源哈希供审计。
schema v24 从 `DT_CombatAwardQuest` 中带有效大陆服开始/结束时间的轨外完成任务导入
`outer_realm_rotation`；schema v25 为怪物模板与等级变体的大小写无关连接补充表达式联合索引；schema v26
从轨外赛季、Buff 配置与曲线表导入当前/下一期 Buff，并只把已审计触发组成写入计算表；schema v27 另保存
轨外怪物池条目的官方本地化名称；schema v28 从 `DT_AdvVision` 与 `DT_AdvVisionMonsterPool` 导入高危委托、
逐难度场景/怪物池与怪物模板；schema v29 从 `DT_BossSupportDataTable` 导入正式 Boss 模板成员，供控制类
条件只按官方成员关系阻断默认成功，不从模板名、中文名或生命值猜 Boss。只有带 1–6 难度显式池映射且模板闭合到属性包的条目进入自动候选，Key=0
通用池只保留为静态来源，不猜测逐难度画像。构建日视为大陆服日切后的有效日期：结束日期等于构建日的旧配置不再进入推断，
按开始时间选出的当前与下一配置分别标记为 `inference_ordinal=0/1`。任务中的 `AbyssID` 关联整套
`AbyssCloneLevelDataTable` 配置，`AbyssLevel` 才是该奖励任务要求完成的层数；不得按配置 ID 数字倒序
代替正式时间区间。
规范化导入器不再次读取 Blueprint JSON，
未导出的引用保留 `target_available = 0`，供推断服务按证据强度处理。
上游 UnrealExporter 配置继续保留 DataTable、Localization/Game、Text、UI、UI_Icon 和 DataAssets 六类
基础导出，并在其上追加全部角色 Skill 动画及少量精确的战斗引用目录；构建器只把其中的结构化 JSON
按表职责入库，PNG 和未进入静态 contract 的 JSON 仍作为后续资源来源保留在导出目录中。
`DT_MonsterPackData_FT` 与 `FT_` 表示 999 夜子玩法；Abyss 的 `AttributeID` 全部关联普通
`DT_MonsterPackData`，不能按文件名或前缀推断场景。

新 SQLite DAO、角色页和 nte-core 同步链路只使用当前发行静态库与原始游戏/nte-core ID，不经过旧格式转换。

## 查询静态数据库

只读 DAO 位于 `src/storage/sqlite/static_game_data_dao.py`。查询脚本只通过 DAO 读取数据库，不会修改数据库，也不读取旧项目 JSON。

先配置数据库路径（下面是示例路径，设置后需要重启 PyCharm）：

```powershell
[Environment]::SetEnvironmentVariable(
  "NTE_GAME_STATIC_DB",
  "build/game_static.sqlite3",
  "User"
)
```

然后可以直接运行查看脚本。省略参数时显示数据集摘要：

```powershell
python tools/game_data/inspect_static_database.py
python tools/game_data/inspect_static_database.py characters
python tools/game_data/inspect_static_database.py shapes
python tools/game_data/inspect_static_database.py suits --id Suit7
python tools/game_data/inspect_static_database.py equipment --id module
python tools/game_data/inspect_static_database.py forks
python tools/game_data/inspect_static_database.py plan --id 1003
python tools/game_data/inspect_static_database.py topple-curve
python tools/game_data/inspect_static_database.py reaction-curve --id GE_ActorReaction_1_Damage
python tools/game_data/inspect_static_database.py reactions
python tools/game_data/inspect_static_database.py combat-constants
python tools/game_data/inspect_static_database.py skill-damage --id GE_Player_Mint_Skill1_Damage_Test1
python tools/game_data/inspect_static_database.py enemy-profile --id standard:Abyss_1_10_boss_09_BP
python tools/game_data/inspect_static_database.py buff --id /Game/Blueprints/Abilities/Fork/Fork_serenity/Buff_Fork_serenity_EquipSkill
python tools/game_data/inspect_static_database.py effect-buffs --id fork_star:upgradestar_pack_fork_jingmotingyuan:1
python tools/game_data/inspect_static_database.py roguelike-modifier --id RG_AtkUp_1
```

DAO 单元测试不依赖 pytest：

```powershell
python -m unittest discover -s tests -p test_static_game_data_dao.py -v
```
