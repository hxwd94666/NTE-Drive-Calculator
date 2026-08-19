-- 用户数据库 v24：角色好感度 10 级开关与可复现的觉醒多选。

ALTER TABLE character_profile
    ADD COLUMN likeability_level_10_enabled INTEGER NOT NULL DEFAULT 0
    CHECK (likeability_level_10_enabled IN (0, 1));

ALTER TABLE character_profile
    ADD COLUMN awakening_selection_initialized INTEGER NOT NULL DEFAULT 0
    CHECK (awakening_selection_initialized IN (0, 1));

CREATE TABLE character_profile_awaken_effect (
    character_id INTEGER NOT NULL
        REFERENCES character_profile(character_id) ON DELETE CASCADE,
    effect_id TEXT NOT NULL CHECK (length(trim(effect_id)) > 0),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (character_id, effect_id),
    UNIQUE (character_id, ordinal)
);
