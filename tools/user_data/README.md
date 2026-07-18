# 用户 SQLite 数据库

每个应用账号使用独立数据库：

```text
accounts/<account_id>/user_data.sqlite3
```

数据库存放用户拥有的数据：同步/装配方式设置、原始背包快照、装备实例词条和保存的装配方案。角色、装备模板、空幕和弧盘的静态名称不复制到这里，只记录原始游戏 ID 或 `nte-core` ID。

## 初始化并导入快照

```powershell
python tools/user_data/manage_user_database.py `
  --database accounts/default/user_data.sqlite3 `
  init --account-id default --account-name "默认账号"

python tools/user_data/manage_user_database.py `
  --database accounts/default/user_data.sqlite3 `
  import-snapshot logs/nte_core/inventory_snapshot_20260718_012112.json
```

## 查看

```powershell
python tools/user_data/manage_user_database.py --database accounts/default/user_data.sqlite3 summary
python tools/user_data/manage_user_database.py --database accounts/default/user_data.sqlite3 check
python tools/user_data/manage_user_database.py --database accounts/default/user_data.sqlite3 snapshots
python tools/user_data/manage_user_database.py --database accounts/default/user_data.sqlite3 inventory --kind module --limit 10
python tools/user_data/manage_user_database.py --database accounts/default/user_data.sqlite3 settings
python tools/user_data/manage_user_database.py --database accounts/default/user_data.sqlite3 plans
```

也可以在 DB Browser for SQLite 中打开 `accounts/default/user_data.sqlite3`。重点表：

- `sync_settings`：背包同步、自动装配和本地数据读取设置。
- `inventory_snapshot`：每次完整背包同步的元数据和原始 JSON。
- `inventory_item`：驱动/核心实例及 `nte-core` UID。
- `inventory_item_stat`：主词条和副词条。
- `current_inventory_item`：当前有效背包视图。
- `loadout_plan`、`loadout_plan_item`：保存的装配方案。

## 测试

```powershell
python -m unittest discover -s tests -p test_user_data_dao.py -v
```
