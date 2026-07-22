-- Abyss 关卡、波次、怪物池和属性包的可追溯绑定。

CREATE TABLE abyss_level (
    level_config_id TEXT NOT NULL,
    level_id INTEGER NOT NULL CHECK (level_id >= 0),
    abyss_id TEXT,
    name_zh TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (level_config_id, level_id)
);

CREATE TABLE abyss_level_monster_spawn (
    level_config_id TEXT NOT NULL,
    level_id INTEGER NOT NULL,
    fight_stage TEXT NOT NULL,
    spawn_ordinal INTEGER NOT NULL CHECK (spawn_ordinal >= 0),
    wave INTEGER,
    monster_pool_id TEXT NOT NULL,
    next_spawn_type TEXT,
    spawn_time REAL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (level_config_id, level_id, fight_stage, spawn_ordinal),
    FOREIGN KEY (level_config_id, level_id)
        REFERENCES abyss_level(level_config_id, level_id)
);

CREATE TABLE abyss_monster_pool_entry (
    monster_pool_id TEXT NOT NULL,
    monster_ordinal INTEGER NOT NULL CHECK (monster_ordinal >= 0),
    monster_class_path TEXT,
    monster_count INTEGER NOT NULL CHECK (monster_count >= 0),
    monster_level INTEGER NOT NULL CHECK (monster_level >= 0),
    attribute_profile_set TEXT NOT NULL
        CHECK (attribute_profile_set IN ('standard', 'night_999')),
    attribute_pack_id TEXT NOT NULL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    attribute_source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (monster_pool_id, monster_ordinal),
    FOREIGN KEY (attribute_profile_set, attribute_pack_id)
        REFERENCES enemy_combat_profile(profile_set, pack_id)
);

CREATE INDEX idx_abyss_level_monster_spawn_pool
    ON abyss_level_monster_spawn(monster_pool_id);
CREATE INDEX idx_abyss_monster_pool_entry_attribute
    ON abyss_monster_pool_entry(attribute_profile_set, attribute_pack_id);
