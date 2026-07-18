-- 用户数据库 v2：增加持续同步自动启动选项，并缩短旧版未配置的默认稳定时间。
ALTER TABLE sync_settings
ADD COLUMN auto_start_inventory_sync INTEGER NOT NULL DEFAULT 0
    CHECK (auto_start_inventory_sync IN (0, 1));

UPDATE sync_settings
SET inventory_settle_seconds = 5.0
WHERE inventory_settle_seconds = 15.0;
