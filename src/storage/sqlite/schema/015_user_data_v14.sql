-- requires-foreign-keys-off
-- 用户数据库 v14：鼠标与手柄全量扫描统一保存为 vision 快照来源。
DROP VIEW current_inventory_item;

CREATE TABLE inventory_snapshot_v14 (
    snapshot_id INTEGER PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('nte_core', 'vision', 'gamepad', 'import')),
    generation INTEGER,
    sequence INTEGER,
    observed_at_unix_ms INTEGER,
    captured_at_utc TEXT NOT NULL,
    complete INTEGER NOT NULL CHECK (complete IN (0, 1)),
    declared_item_count INTEGER NOT NULL CHECK (declared_item_count >= 0),
    stored_item_count INTEGER NOT NULL CHECK (stored_item_count >= 0),
    protocol_version INTEGER,
    raw_snapshot_json TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0, 1)),
    created_at_utc TEXT NOT NULL,
    CHECK (complete = 0 OR declared_item_count = stored_item_count)
);

INSERT INTO inventory_snapshot_v14(
    snapshot_id, source, generation, sequence, observed_at_unix_ms,
    captured_at_utc, complete, declared_item_count, stored_item_count,
    protocol_version, raw_snapshot_json, is_current, created_at_utc
)
SELECT
    snapshot_id, source, generation, sequence, observed_at_unix_ms,
    captured_at_utc, complete, declared_item_count, stored_item_count,
    protocol_version, raw_snapshot_json, is_current, created_at_utc
FROM inventory_snapshot;

DROP TABLE inventory_snapshot;
ALTER TABLE inventory_snapshot_v14 RENAME TO inventory_snapshot;

CREATE UNIQUE INDEX idx_inventory_snapshot_one_current
    ON inventory_snapshot(is_current) WHERE is_current = 1;
CREATE INDEX idx_inventory_snapshot_captured
    ON inventory_snapshot(captured_at_utc DESC, snapshot_id DESC);

CREATE VIEW current_inventory_item AS
SELECT item.*
FROM inventory_item AS item
JOIN inventory_snapshot AS snapshot USING (snapshot_id)
WHERE snapshot.is_current = 1;
