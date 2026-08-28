-- 静态库 v28：高危委托、逐难度怪物池与正式怪物模板。

CREATE TABLE high_risk_commission (
    commission_id TEXT PRIMARY KEY,
    name_zh TEXT NOT NULL CHECK (length(trim(name_zh)) > 0),
    difficulty_count INTEGER NOT NULL CHECK (difficulty_count >= 1),
    fallback_monster_pool_id TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE high_risk_commission_difficulty (
    commission_id TEXT NOT NULL REFERENCES high_risk_commission(commission_id),
    difficulty_id INTEGER NOT NULL CHECK (difficulty_id >= 1),
    recommended_character_level INTEGER NOT NULL CHECK (
        recommended_character_level >= 0
    ),
    scene_data_id TEXT,
    monster_pool_id TEXT,
    PRIMARY KEY (commission_id, difficulty_id)
);

CREATE INDEX idx_high_risk_commission_difficulty_pool
    ON high_risk_commission_difficulty(monster_pool_id);

CREATE TABLE high_risk_monster_pool_member (
    monster_pool_id TEXT NOT NULL,
    member_ordinal INTEGER NOT NULL CHECK (member_ordinal >= 0),
    monster_class_path TEXT NOT NULL CHECK (
        length(trim(monster_class_path)) > 0
    ),
    monster_template_name TEXT NOT NULL CHECK (
        length(trim(monster_template_name)) > 0
    ),
    monster_count INTEGER NOT NULL CHECK (monster_count >= 1),
    configured_monster_level INTEGER NOT NULL CHECK (
        configured_monster_level >= 0
    ),
    attribute_id TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (monster_pool_id, member_ordinal)
);

CREATE INDEX idx_high_risk_monster_pool_member_template
    ON high_risk_monster_pool_member(monster_template_name);
