# 游戏静态数据开发工具

这里仅保存可公开审查的数据整理、校验代码和开发者注释，不保存游戏官方文件、由其整理出的原始 JSON、图片或生成的数据库。

## 数据原则

- 游戏记录、ID、名称和关系只来自开发者本机的游戏官方数据文件。
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

## 构建静态 SQLite v2

```powershell
python tools/game_data/build_static_database.py `
  --source $gameDataSource `
  --output "$gameDataWorkspace\build\game_static.sqlite3" `
  --report-dir "$gameDataWorkspace\reports\static_database" `
  --dataset-id "unversioned_20260718" `
  --as-of 2026-07-18
```

生成的数据库放在项目外。每次游戏版本更新时，开发者从本机游戏官方数据文件重新整理数据库，检查来源哈希、数量和外键后再发布允许分发的数据集。游戏官方文件和中间数据不进入开源仓库。

当前旧版应用仍读取旧 JSON。后续 SQLite DAO、新主页和 nte-core 同步链路只使用 v2 数据库与原始游戏/nte-core ID，不经过旧格式转换。

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
```

DAO 单元测试不依赖 pytest：

```powershell
python -m unittest discover -s tests -p test_static_game_data_dao.py -v
```
