-- 静态库 v14：角色额外形状标签及其固定属性加成；运行时只读。

CREATE TABLE character_shape_bonus (
    character_id INTEGER PRIMARY KEY REFERENCES character(character_id),
    shape_label TEXT NOT NULL,
    shape_grid_count INTEGER NOT NULL CHECK (shape_grid_count > 0),
    source_kind TEXT NOT NULL
        CHECK (source_kind IN ('official_role_profile', 'legacy_role_profile'))
);

CREATE TABLE character_shape_bonus_property (
    character_id INTEGER NOT NULL
        REFERENCES character_shape_bonus(character_id) ON DELETE CASCADE,
    property_id TEXT NOT NULL REFERENCES equipment_attribute(attribute_id),
    display_value REAL NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (character_id, property_id),
    UNIQUE (character_id, ordinal)
);

CREATE INDEX idx_character_shape_bonus_property_character
    ON character_shape_bonus_property(character_id, ordinal);
