# 游戏静态数据开发工具

这里仅保存可公开审查的数据整理、校验代码和开发者注释，不保存游戏官方文件、由其整理出的原始 JSON、图片或生成的数据库。

## 数据原则

- 游戏记录、ID、名称和关系只来自开发者本机的游戏官方数据文件；角色推荐权重是单独标注来源的工坊 API 开发期快照，不冒充游戏官方字段。
- 标准化查询表以原始游戏 ID 为主键，同时在 `source_row.payload_json` 保留完整来源行。
- 人工确认的特殊角色分类单独进入 `character_annotation`，不能修改原始角色记录。
- 新数据库和新运行链路不读取旧 `roles.json`、`sets.json`、`shapes.json`、`tapes.json` 或 `weapons.json`。

## 角色数据源清单

```powershell
$gameDataSource = "D:\path\to\game_official_data\Content"
$gameDataWorkspace = "D:\path\to\game_data_workspace"

python tools/game_data/catalog_characters.py `
  --source $gameDataSource `
  --output-dir "$gameDataWorkspace\reports\characters" `
  --as-of 2026-07-18
```

分类规则位于 `character_overrides.json`。它只补充特殊形态和玩法配置的分类，不提供游戏名称，也不决定角色是否存在。

## 构建静态 SQLite v11

```powershell
python tools/game_data/build_static_database.py `
  --source $gameDataSource `
  --output "$gameDataWorkspace\build\game_static.sqlite3" `
  --report-dir "$gameDataWorkspace\reports\static_database" `
  --dataset-id "release_20260718" `
  --as-of 2026-07-18
```

提交到项目并随安装包分发的数据库必须省略来源行原文：

```powershell
python tools/game_data/build_static_database.py `
  --source $gameDataSource `
  --output "data\game_static.sqlite3" `
  --report-dir "$gameDataWorkspace\reports\distribution_database" `
  --dataset-id "release_20260718" `
  --as-of 2026-07-18 `
  --omit-source-payloads
```

发行数据库仍保留来源文件相对路径、文件哈希、来源行键和内容哈希，但
`source_row.payload_json` 为 `NULL`。完整来源内容只保留在开发者工作区。

完整审计数据库放在项目外；省略来源行原文的发行数据库放在
`data/game_static.sqlite3`。每次游戏版本更新时，开发者从本机游戏官方数据文件重新整理数据库，检查来源哈希、数量和外键后再更新发行数据库。游戏官方文件和中间数据不进入开源仓库。

构建完成后，开发发布模式使用异环工坊 Open API Key 原子更新角色推荐权重：

```powershell
python tools/game_data/sync_recommended_weights.py `
  --database data/game_static.sqlite3
```

API 没有返回的角色会写入固定默认权重（增伤、暴击、爆伤、攻击力%）。Key 只从开发机环境或 `.env` 读取，不写入数据库或安装包；应用运行时只读静态库，不访问 API，也不读取旧 `roles.json` 权重。

构建器还会自动扫描 `DataTable/Character/Awaken/*AwakenEffect*.json`：每个角色的六个
可选觉醒、三/六觉共鸣、名称/描述/图标、Buff 引用和明确的技能等级加成都会进入静态库。
用户拥有的副本数和实际激活的觉醒属于账号私有计算配置，不写入发行静态库。

角色基础成长由 `DT_Character.ElementData.PropModifyID` 关联
`DT_PlayerPackData.json` 的 `*_base` 行与 `DT_PlayerModifyPackData.json` 的
`*_lv_1..80`、`*_stage_1..6` 累计修改行生成。构建器为每位角色输出 86 条有效状态：
普通等级、六个突破等级各一条突破前/后状态，以及满级状态。

角色技能目录来自 `DT_CharacterAbilityConfig.json`，每个技能记录官方技能 ID、类型、顺序、
显示标记和所有等级的突破/觉醒要求及材料。若
`DT_CharacterAbilityEffectConfig.json` 有对应记录，还会写入技能标签和 Gameplay Effect 资源路径；
未配置技能表的角色不会阻断整库构建。

`DataTable/skill/DT_SkillDamageData.json` 的伤害执行记录和
`DT_SkillDamageGameplayModifyData.json` 的攻击倍率修正也会写入静态库。它们只按
`GAName` 关联既有技能，保留等级数组、元素和破坏参数；构建器与 DAO 均不计算直接伤害。

`character_overrides.json` 中标记为 `combat_transformation` 的记录只保留官方角色目录和
规范角色关联；它们共用规范角色的属性与养成，不能生成独立的成长、觉醒或普通技能目录。

schema v8–v10 新增倾陷/环合曲线、敌方属性包、怪物实例等级变体和 Abyss 关卡绑定；schema v11 新增带来源标记的角色推荐权重。
`DT_MonsterPackData_FT` 与 `FT_` 表示 999 夜子玩法；Abyss 的 `AttributeID` 全部关联普通
`DT_MonsterPackData`，不能按文件名或前缀推断场景。

新 SQLite DAO、角色页和 nte-core 同步链路只使用 v11 数据库与原始游戏/nte-core ID，不经过旧格式转换。

## 查询静态数据库

只读 DAO 位于 `src/storage/sqlite/static_game_data_dao.py`。查询脚本只通过 DAO 读取数据库，不会修改数据库，也不读取旧项目 JSON。

先配置数据库路径（下面是示例路径，设置后需要重启 PyCharm）：

```powershell
[Environment]::SetEnvironmentVariable(
  "NTE_GAME_STATIC_DB",
  "D:\path\to\game_data_workspace\build\game_static.sqlite3",
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
```

DAO 单元测试不依赖 pytest：

```powershell
python -m unittest discover -s tests -p test_static_game_data_dao.py -v
```
