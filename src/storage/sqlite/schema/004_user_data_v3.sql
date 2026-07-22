-- 用户数据库 v3：限制持续背包同步产生的历史快照增长。
-- 当前快照和已保存装配方案引用的快照始终由 DAO 保护，不受该数量限制影响。
ALTER TABLE sync_settings
ADD COLUMN inventory_snapshot_retention_count INTEGER NOT NULL DEFAULT 20
    CHECK (inventory_snapshot_retention_count >= 1);
