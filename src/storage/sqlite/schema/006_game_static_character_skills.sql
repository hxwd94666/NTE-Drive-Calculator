-- 静态库 v6：角色技能目录、技能类型与升级条件。

CREATE TABLE character_skill (
    character_id INTEGER NOT NULL REFERENCES character(character_id),
    skill_id TEXT NOT NULL,
    ability_type TEXT NOT NULL,
    ability_index INTEGER NOT NULL,
    show_detail_info INTEGER NOT NULL CHECK (show_detail_info IN (0, 1)),
    gameplay_tag TEXT,
    gameplay_effect_path TEXT,
    reapply_after_revive INTEGER NOT NULL CHECK (reapply_after_revive IN (0, 1)),
    ability_source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    effect_source_row_id INTEGER REFERENCES source_row(source_row_id),
    PRIMARY KEY (character_id, skill_id)
);

CREATE TABLE character_skill_level (
    character_id INTEGER NOT NULL,
    skill_id TEXT NOT NULL,
    level INTEGER NOT NULL CHECK (level > 0),
    required_breakthrough_stage INTEGER NOT NULL CHECK (required_breakthrough_stage BETWEEN 0 AND 6),
    required_awaken_level INTEGER NOT NULL CHECK (required_awaken_level BETWEEN 0 AND 6),
    cost_items_json TEXT NOT NULL,
    PRIMARY KEY (character_id, skill_id, level),
    FOREIGN KEY (character_id, skill_id)
        REFERENCES character_skill(character_id, skill_id)
);

CREATE INDEX idx_character_skill_character
    ON character_skill(character_id, ability_index, skill_id);
