PRAGMA foreign_keys = ON;

-- 每个应用账号各自拥有一份用户数据库。静态游戏记录保存在
-- game_static.sqlite3 中，用户数据库只引用其中的原始游戏 ID。
CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY,
    applied_at_utc TEXT NOT NULL
);

CREATE TABLE database_profile (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    account_id TEXT NOT NULL,
    account_name TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE sync_settings (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    inventory_sync_method TEXT NOT NULL
        CHECK (inventory_sync_method IN ('nte_core', 'gamepad')),
    equipment_apply_method TEXT NOT NULL
        CHECK (equipment_apply_method IN ('nte_core', 'gamepad')),
    capture_device_id TEXT,
    raw_capture_enabled INTEGER NOT NULL DEFAULT 0
        CHECK (raw_capture_enabled IN (0, 1)),
    inventory_settle_seconds REAL NOT NULL DEFAULT 15.0
        CHECK (inventory_settle_seconds >= 0),
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE inventory_snapshot (
    snapshot_id INTEGER PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('nte_core', 'gamepad', 'import')),
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

CREATE UNIQUE INDEX idx_inventory_snapshot_one_current
    ON inventory_snapshot(is_current) WHERE is_current = 1;
CREATE INDEX idx_inventory_snapshot_captured
    ON inventory_snapshot(captured_at_utc DESC, snapshot_id DESC);

CREATE TABLE inventory_item (
    snapshot_id INTEGER NOT NULL
        REFERENCES inventory_snapshot(snapshot_id) ON DELETE CASCADE,
    uid_serial INTEGER NOT NULL,
    uid_slot INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('module', 'core')),
    item_id TEXT NOT NULL,
    suit_id TEXT,
    geometry TEXT,
    grid_count INTEGER,
    quality TEXT,
    level INTEGER NOT NULL CHECK (level >= 0),
    max_level INTEGER NOT NULL CHECK (max_level >= level),
    locked INTEGER NOT NULL CHECK (locked IN (0, 1)),
    equipped INTEGER NOT NULL CHECK (equipped IN (0, 1)),
    equipped_character_uid_json TEXT,
    equipped_character_id INTEGER,
    names_json TEXT NOT NULL,
    suit_names_json TEXT NOT NULL,
    raw_item_json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, uid_serial, uid_slot)
);

CREATE INDEX idx_inventory_item_kind
    ON inventory_item(snapshot_id, kind);
CREATE INDEX idx_inventory_item_template
    ON inventory_item(item_id);
CREATE INDEX idx_inventory_item_suit
    ON inventory_item(suit_id);
CREATE INDEX idx_inventory_item_equipped_character
    ON inventory_item(equipped_character_id);

CREATE TABLE inventory_item_stat (
    snapshot_id INTEGER NOT NULL,
    uid_serial INTEGER NOT NULL,
    uid_slot INTEGER NOT NULL,
    stat_group TEXT NOT NULL CHECK (stat_group IN ('main', 'sub')),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    property_id TEXT NOT NULL,
    value REAL NOT NULL,
    is_percent INTEGER NOT NULL CHECK (is_percent IN (0, 1)),
    names_json TEXT NOT NULL,
    raw_stat_json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, uid_serial, uid_slot, stat_group, ordinal),
    FOREIGN KEY (snapshot_id, uid_serial, uid_slot)
        REFERENCES inventory_item(snapshot_id, uid_serial, uid_slot)
        ON DELETE CASCADE
);

CREATE INDEX idx_inventory_item_stat_property
    ON inventory_item_stat(property_id);

CREATE TABLE loadout_plan (
    plan_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    character_id INTEGER NOT NULL,
    source_snapshot_id INTEGER
        REFERENCES inventory_snapshot(snapshot_id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    score REAL,
    payload_json TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_loadout_plan_active_character
    ON loadout_plan(character_id) WHERE is_active = 1;
CREATE INDEX idx_loadout_plan_character
    ON loadout_plan(character_id, updated_at_utc DESC);

CREATE TABLE loadout_plan_item (
    plan_id INTEGER NOT NULL REFERENCES loadout_plan(plan_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    uid_serial INTEGER NOT NULL,
    uid_slot INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('module', 'core')),
    target_row INTEGER CHECK (target_row BETWEEN 1 AND 5),
    target_column INTEGER CHECK (target_column BETWEEN 1 AND 5),
    rotation INTEGER,
    raw_assignment_json TEXT NOT NULL,
    PRIMARY KEY (plan_id, ordinal),
    UNIQUE (plan_id, uid_serial, uid_slot)
);

CREATE VIEW current_inventory_item AS
SELECT item.*
FROM inventory_item AS item
JOIN inventory_snapshot AS snapshot USING (snapshot_id)
WHERE snapshot.is_current = 1;
