-- 用户数据库 v26：战报角色配置保留原始快照，并只允许一个可覆盖的用户修改副本。

CREATE TABLE battle_build_edit (
    battle_record_id INTEGER PRIMARY KEY
        REFERENCES battle_build_snapshot(battle_record_id) ON DELETE CASCADE,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE battle_character_build_edit (
    battle_record_id INTEGER NOT NULL
        REFERENCES battle_build_edit(battle_record_id) ON DELETE CASCADE,
    character_id INTEGER NOT NULL CHECK (character_id > 0),
    observed_name TEXT,
    profile_source TEXT NOT NULL
        CHECK (profile_source = 'user_edited_snapshot'),
    character_level INTEGER NOT NULL CHECK (character_level BETWEEN 1 AND 80),
    breakthrough_stage INTEGER NOT NULL CHECK (breakthrough_stage BETWEEN 0 AND 6),
    awakening_level INTEGER NOT NULL CHECK (awakening_level BETWEEN 0 AND 6),
    likeability_level_10_enabled INTEGER NOT NULL
        CHECK (likeability_level_10_enabled IN (0, 1)),
    fork_id TEXT,
    fork_level INTEGER,
    fork_refinement_level INTEGER,
    selected_skill_id TEXT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    raw_profile_json TEXT NOT NULL CHECK (json_valid(raw_profile_json)),
    PRIMARY KEY (battle_record_id, character_id),
    FOREIGN KEY (battle_record_id, character_id)
        REFERENCES battle_character_build_snapshot(battle_record_id, character_id)
        ON DELETE CASCADE
);

CREATE TABLE battle_character_skill_edit (
    battle_record_id INTEGER NOT NULL,
    character_id INTEGER NOT NULL,
    skill_id TEXT NOT NULL CHECK (length(trim(skill_id)) > 0),
    skill_level INTEGER NOT NULL CHECK (skill_level BETWEEN 1 AND 11),
    PRIMARY KEY (battle_record_id, character_id, skill_id),
    FOREIGN KEY (battle_record_id, character_id)
        REFERENCES battle_character_build_edit(battle_record_id, character_id)
        ON DELETE CASCADE
);

CREATE TABLE battle_character_awaken_edit (
    battle_record_id INTEGER NOT NULL,
    character_id INTEGER NOT NULL,
    effect_id TEXT NOT NULL CHECK (length(trim(effect_id)) > 0),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (battle_record_id, character_id, effect_id),
    FOREIGN KEY (battle_record_id, character_id)
        REFERENCES battle_character_build_edit(battle_record_id, character_id)
        ON DELETE CASCADE
);
