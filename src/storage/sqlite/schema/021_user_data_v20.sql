-- 用户数据库 v20：自建角色在基础权重页选择的默认计算套装。
CREATE TABLE IF NOT EXISTS custom_character_calculation_setting (
    character_id INTEGER PRIMARY KEY
        REFERENCES custom_character(character_id) ON DELETE CASCADE,
    target_suit_id TEXT,
    updated_at_utc TEXT NOT NULL,
    CHECK (target_suit_id IS NULL OR length(trim(target_suit_id)) > 0)
);
