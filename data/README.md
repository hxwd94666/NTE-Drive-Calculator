# 发行版静态游戏数据库

`game_static.sqlite3` 是应用随安装包分发的只读基础数据库。它由开发者从
本机准备好的游戏官方文件生成，普通用户不需要另外下载。

当前数据集：`unversioned_20260722_combat`
当前结构版本：`5`
当前 SHA-256：`D4D5C432D5DAA2B9FF0C238E2BD5F2E6AE54570F2BF27D7A77D5B43A9DF8F559`

发行数据库保留规范化业务表、来源文件相对路径、来源文件哈希、来源行键和
来源行内容哈希。`source_row.payload_json` 必须全部为 `NULL`，完整来源原文和
构建报告只保存在开发者工作区，不进入项目仓库。

schema v3 已包含倾陷等级曲线、环合配置、技能伤害倍率和敌方战斗属性包；schema v5 新增 Abyss 关卡、波次、怪物池和属性包绑定。`FT_` 前缀属于 999 夜子玩法，不作为 Abyss 或轨外之境场景依据。反应与技能数组在
没有确切等级映射时仅保留官方档位序号，不将数组位置推测为等级。

重新生成时使用：

```powershell
python tools/game_data/build_static_database.py `
  --source "D:\path\to\game_official_data\Content" `
  --output "data\game_static.sqlite3" `
  --report-dir "D:\path\to\game_data_workspace\reports\distribution_database" `
  --dataset-id "game-version_and_date" `
  --game-version "game-version" `
  --as-of 2026-07-19 `
  --omit-source-payloads
```

生成后必须运行静态数据库测试，并同步更新本文件中的数据集和 SHA-256。
