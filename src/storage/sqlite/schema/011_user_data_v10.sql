-- 用户数据库 v10：账号可覆写角色额外形状标签与属性加成。

CREATE TABLE character_shape_bonus_preference (
    character_id INTEGER PRIMARY KEY CHECK (character_id > 0),
    shape_label TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE character_shape_bonus_preference_property (
    character_id INTEGER NOT NULL
        REFERENCES character_shape_bonus_preference(character_id) ON DELETE CASCADE,
    property_id TEXT NOT NULL,
    display_value REAL NOT NULL CHECK (display_value >= 0),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (character_id, property_id),
    UNIQUE (character_id, ordinal)
);

CREATE INDEX idx_character_shape_bonus_preference_property_character
    ON character_shape_bonus_preference_property(character_id, ordinal);
