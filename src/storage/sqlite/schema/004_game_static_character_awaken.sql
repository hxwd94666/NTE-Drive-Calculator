-- 静态库 v4：角色六觉、三觉/六觉共鸣，以及共鸣附带的技能等级加成。

CREATE TABLE character_awaken_effect (
    character_id INTEGER NOT NULL REFERENCES character(character_id),
    effect_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    awaken_type TEXT NOT NULL,
    title_zh TEXT,
    title_text_table TEXT,
    title_text_key TEXT,
    description_zh TEXT,
    description_text_table TEXT,
    description_text_key TEXT,
    icon_path TEXT,
    modify_data_json TEXT NOT NULL,
    gameplay_effect_ids_json TEXT NOT NULL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (character_id, effect_id),
    UNIQUE (character_id, ordinal)
);

CREATE TABLE character_awaken_skill_level_bonus (
    character_id INTEGER NOT NULL,
    effect_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    skill_id TEXT NOT NULL,
    level_delta INTEGER NOT NULL,
    PRIMARY KEY (character_id, effect_id, ordinal),
    FOREIGN KEY (character_id, effect_id)
        REFERENCES character_awaken_effect(character_id, effect_id)
);

CREATE INDEX idx_character_awaken_effect_character
    ON character_awaken_effect(character_id, ordinal);
