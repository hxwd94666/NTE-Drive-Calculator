-- 静态库 v22：争锋赏宴、场景选择与魔女赐福目录。

CREATE TABLE feast_stage (
    stage_id TEXT PRIMARY KEY,
    name_zh TEXT NOT NULL CHECK (length(trim(name_zh)) > 0),
    boss_monster_id TEXT NOT NULL,
    special_high_difficulty INTEGER NOT NULL CHECK (special_high_difficulty IN (0, 1)),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);
CREATE TABLE feast_stage_difficulty (
    stage_id TEXT NOT NULL REFERENCES feast_stage(stage_id),
    difficulty_id INTEGER NOT NULL CHECK (difficulty_id BETWEEN 1 AND 4),
    name_zh TEXT NOT NULL,
    boss_name_zh TEXT NOT NULL,
    base_score INTEGER NOT NULL,
    score_rate REAL NOT NULL,
    monster_level INTEGER NOT NULL,
    profile_set TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    boss_icon_path TEXT,
    PRIMARY KEY (stage_id, difficulty_id)
);

CREATE TABLE feast_option (
    option_id TEXT PRIMARY KEY,
    option_type TEXT NOT NULL,
    effect_kind TEXT NOT NULL,
    damage_type TEXT,
    add_value REAL,
    limit_seconds INTEGER,
    score INTEGER NOT NULL,
    buff_asset_path TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE feast_stage_option (
    stage_id TEXT NOT NULL REFERENCES feast_stage(stage_id),
    category_ordinal INTEGER NOT NULL CHECK (category_ordinal >= 0),
    option_ordinal INTEGER NOT NULL CHECK (option_ordinal >= 0),
    category_name_zh TEXT NOT NULL,
    option_id TEXT NOT NULL REFERENCES feast_option(option_id),
    PRIMARY KEY (stage_id, category_ordinal, option_ordinal)
);

CREATE TABLE divination_buff (
    buff_id TEXT PRIMARY KEY,
    name_zh TEXT NOT NULL,
    description_zh TEXT NOT NULL,
    property_id TEXT NOT NULL,
    property_value REAL NOT NULL,
    is_percent INTEGER NOT NULL CHECK (is_percent IN (0, 1)),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);
