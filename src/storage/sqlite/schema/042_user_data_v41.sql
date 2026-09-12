-- 用户数据库 v41：仅保存原生正式投影实际观测到的角色等级与突破阶段。
CREATE TABLE character_profile_observation (
    character_id INTEGER PRIMARY KEY CHECK (character_id > 0),
    character_level INTEGER CHECK (character_level BETWEEN 1 AND 80),
    breakthrough_stage INTEGER CHECK (breakthrough_stage BETWEEN 0 AND 6),
    updated_at_utc TEXT NOT NULL,
    CHECK (character_level IS NOT NULL OR breakthrough_stage IS NOT NULL)
);
