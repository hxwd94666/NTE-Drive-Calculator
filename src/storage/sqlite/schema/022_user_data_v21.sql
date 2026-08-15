-- 用户数据库 v21：完整库存快照上的临时游戏状态增量。
-- 残缺抓包只能更新已知装备的状态，绝不能成为新的库存集合或当前快照。
CREATE TABLE inventory_item_runtime_state (
    snapshot_id INTEGER NOT NULL,
    uid_serial INTEGER NOT NULL,
    uid_slot INTEGER NOT NULL,
    locked INTEGER NOT NULL CHECK (locked IN (0, 1)),
    discarded INTEGER NOT NULL CHECK (discarded IN (0, 1)),
    equipped INTEGER NOT NULL CHECK (equipped IN (0, 1)),
    equipped_character_uid_json TEXT,
    equipped_character_id INTEGER,
    equipped_placement_json TEXT,
    observed_at_unix_ms INTEGER,
    sequence INTEGER,
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, uid_serial, uid_slot),
    FOREIGN KEY (snapshot_id, uid_serial, uid_slot)
        REFERENCES inventory_item(snapshot_id, uid_serial, uid_slot)
        ON DELETE CASCADE
);

CREATE INDEX idx_inventory_item_runtime_state_snapshot
    ON inventory_item_runtime_state(snapshot_id);
