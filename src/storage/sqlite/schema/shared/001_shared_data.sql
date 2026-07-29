-- 本机公共覆盖库 v1：保存跨账号共享且可删除的用户覆盖。

CREATE TABLE database_profile (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    database_kind TEXT NOT NULL CHECK (database_kind = 'app_shared'),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE logical_character_shape_bonus_override (
    logical_character_key TEXT PRIMARY KEY,
    representative_character_id INTEGER NOT NULL
        CHECK (representative_character_id > 0),
    shape_label TEXT NOT NULL,
    shape_grid_count INTEGER NOT NULL CHECK (shape_grid_count > 0),
    based_on_dataset_id TEXT,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE logical_character_shape_bonus_property_override (
    logical_character_key TEXT NOT NULL
        REFERENCES logical_character_shape_bonus_override(logical_character_key)
        ON DELETE CASCADE,
    property_id TEXT NOT NULL,
    display_value REAL NOT NULL CHECK (display_value >= 0),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (logical_character_key, property_id),
    UNIQUE (logical_character_key, ordinal)
);

CREATE INDEX idx_shared_shape_bonus_property_order
    ON logical_character_shape_bonus_property_override(
        logical_character_key, ordinal
    );
