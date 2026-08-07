# 数据存储

## 模块定位

项目将发行静态、本机共享和账号数据分成三个 SQLite 数据域，DAO 独占 SQL、事务和迁移。

## 当前数据域

| 数据域 | 文件 | 写入者 |
| --- | --- | --- |
| 发行静态 | `data/game_static.sqlite3` | 仅 `tools/game_data` |
| 本机共享 | `app_shared.sqlite3` | `SharedDataDao` |
| 账号数据 | `accounts/<account_id>/user_data.sqlite3` | `UserDataDao` 和应用服务 |

发行静态库存放官方角色、装备、技能、敌人、推荐权重和默认图纸；共享库当前主要保存额外形状
差异；账号库保存背包快照、角色配置、权重、配装、装配任务、设置和战报历史。

## 当前能力

- 静态库运行时只读；
- 共享覆盖按差异保存，删除覆盖恢复发行默认；
- 账号库通过顺序迁移升级，已发布迁移含义不可修改；
- 新快照、配装和战报均通过事务提交；
- `data/manifest.json` 校验静态库 dataset、schema 和 SHA-256。

## 本地数据边界

账号库、WAL/SHM、日志、截图、逐击 JSON、PCAP 和本地生成数据库都不得提交。个人战报样本放在
`local_game_data/`。发行静态库和明确晋升的 `third_party` 组件除外。

## 当前限制

- 静态库更新不是运行时功能；
- 上游提交若只更新静态库，维护者仍必须同步 manifest；
- 页面和 Service 不得直接拼 SQL。

## 验证

主要覆盖 migration、static-data、manifest、账号隔离和失败回滚测试。

## 主要实现

`src/storage/sqlite/`、`tools/game_data/`、`data/manifest.json`。
