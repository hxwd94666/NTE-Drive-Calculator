# 发行版静态游戏数据库

`game_static.sqlite3` 是应用随安装包分发的只读基础数据库。它由开发者从
本机准备好的游戏官方文件生成，普通用户不需要另外下载。

当前数据集：`unversioned_20260718`
当前 SHA-256：`1CE3484BEBD2D03E119FA0A5ED3E1339433F71F71CE68AFC6B4EC660B8B79EB5`

发行数据库保留规范化业务表、来源文件相对路径、来源文件哈希、来源行键和
来源行内容哈希。`source_row.payload_json` 必须全部为 `NULL`，完整来源原文和
构建报告只保存在开发者工作区，不进入项目仓库。

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
