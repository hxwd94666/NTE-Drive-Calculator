# 背包同步

## 模块定位

通过 nte-core 或视觉扫描产生稳定、不可变的账号背包快照，为仓库、计算和装配提供固定输入。

## 当前能力

- `capture.start(profile="inventory")` 接收 nte-core 快照事件；
- `InventorySnapshotStabilizer` 等待完整且内容稳定的候选；
- `InventorySyncService` 一次事务提交装备、角色实例和当前指针；
- `inventory.get_latest` 读取 nte-core 最近捕获的数据；
- 独立 `characters` 列表用于极速装配角色 UID 完整性；
- 账号内保留版本化快照并暴露固定 `snapshot_id`。

## 数据边界

- 写入当前账号库；
- nte-core 和视觉库存共享快照表，但 `source` 能力不同；
- 视觉临时 UID 不能用于极速装配；
- 下游在操作开始前解析一次当前快照，运行中不追随最新指针。

## 生命周期

同一账号同一时刻只有一个同步实例。账号切换、战报独占 combat 会话和应用退出都必须通过公开
停止入口处理；旧 generation 事件不能提交。

## 当前限制

- `inventory.get_latest` 不是强制游戏刷新；
- RPC 接收或候选事件不等于稳定快照已提交；
- 视觉来源不具备完整角色实例和可靠状态写回能力。

## 验证

主要覆盖 `test_inventory_sync_service`、`test_inventory_snapshot_stabilizer` 和账号快照 DAO。

## 主要实现

`src/services/inventory_sync_*`、`src/storage/sqlite/inventory_snapshot_dao.py`、
`src/integrations/nte_core.py`。
