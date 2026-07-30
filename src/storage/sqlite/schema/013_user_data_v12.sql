-- 用户数据库 v12：为活动配装方案增加计算保留锁。
ALTER TABLE loadout_plan
    ADD COLUMN allocation_locked INTEGER NOT NULL DEFAULT 0
    CHECK (allocation_locked IN (0, 1));

CREATE INDEX idx_loadout_plan_active_allocation_locked
    ON loadout_plan(allocation_locked, updated_at_utc DESC)
    WHERE is_active = 1 AND allocation_locked = 1;
