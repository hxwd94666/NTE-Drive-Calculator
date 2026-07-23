-- 静态库 v15：按逻辑角色保存共享形状规则，角色变体运行时自动解析。

CREATE TABLE logical_character_shape_bonus (
    logical_character_key TEXT PRIMARY KEY,
    representative_character_id INTEGER NOT NULL REFERENCES character(character_id),
    shape_label TEXT NOT NULL,
    shape_grid_count INTEGER NOT NULL CHECK (shape_grid_count > 0),
    source_kind TEXT NOT NULL
        CHECK (source_kind IN ('official_role_profile', 'legacy_role_profile'))
);

CREATE TABLE logical_character_shape_bonus_property (
    logical_character_key TEXT NOT NULL
        REFERENCES logical_character_shape_bonus(logical_character_key) ON DELETE CASCADE,
    property_id TEXT NOT NULL REFERENCES equipment_attribute(attribute_id),
    display_value REAL NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (logical_character_key, property_id),
    UNIQUE (logical_character_key, ordinal)
);

CREATE INDEX idx_logical_character_shape_bonus_property
    ON logical_character_shape_bonus_property(logical_character_key, ordinal);
