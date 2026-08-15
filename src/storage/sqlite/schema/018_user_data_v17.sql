-- 用户数据库 v17：账号私有的自建可计算角色。
CREATE TABLE IF NOT EXISTS custom_character (
    character_id INTEGER PRIMARY KEY,
    name_zh TEXT NOT NULL COLLATE NOCASE UNIQUE,
    game_name TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    CHECK (character_id >= 1500000000),
    CHECK (length(trim(name_zh)) BETWEEN 1 AND 40),
    CHECK (length(trim(game_name)) BETWEEN 1 AND 40)
);
