-- 用户数据库 v19：自建角色的账号私有额外形状设置。
CREATE TABLE IF NOT EXISTS custom_character_shape_bonus (
    character_id INTEGER PRIMARY KEY
        REFERENCES custom_character(character_id) ON DELETE CASCADE,
    shape_label TEXT NOT NULL DEFAULT 'Type-3',
    property_id TEXT,
    display_value REAL NOT NULL DEFAULT 0 CHECK (display_value >= 0),
    updated_at_utc TEXT NOT NULL,
    CHECK (length(trim(shape_label)) BETWEEN 1 AND 40),
    CHECK (property_id IS NOT NULL OR display_value = 0)
);
