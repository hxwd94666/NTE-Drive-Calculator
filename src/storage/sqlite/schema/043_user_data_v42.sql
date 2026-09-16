-- 用户数据库 v42：保存已观测的技能、好感度和弧盘；空弧盘与未知字段分开。
ALTER TABLE character_profile_observation RENAME TO character_profile_observation_v41;
CREATE TABLE character_profile_observation (
    character_id INTEGER PRIMARY KEY CHECK (character_id > 0),
    character_level INTEGER CHECK (character_level BETWEEN 1 AND 80),
    breakthrough_stage INTEGER CHECK (breakthrough_stage BETWEEN 0 AND 6),
    cultivation_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(cultivation_json)),
    updated_at_utc TEXT NOT NULL
);
INSERT INTO character_profile_observation(character_id, character_level, breakthrough_stage, updated_at_utc)
SELECT character_id, character_level, breakthrough_stage, updated_at_utc FROM character_profile_observation_v41;
DROP TABLE character_profile_observation_v41;
