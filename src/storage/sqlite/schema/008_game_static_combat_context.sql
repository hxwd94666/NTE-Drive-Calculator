-- 静态库 v8：战斗等级曲线、环合定义和敌方战斗属性包。

CREATE TABLE combat_level_curve (
    curve_id TEXT PRIMARY KEY,
    damage_kind TEXT NOT NULL CHECK (damage_kind IN ('topple', 'reaction')),
    reaction_type TEXT,
    source_effect_id TEXT,
    interpolation_mode TEXT,
    mapping_status TEXT NOT NULL CHECK (mapping_status IN ('exact_level', 'source_tier')),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE combat_level_curve_point (
    curve_id TEXT NOT NULL REFERENCES combat_level_curve(curve_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    character_level REAL,
    source_tier INTEGER,
    value REAL NOT NULL CHECK (value >= 0),
    PRIMARY KEY (curve_id, ordinal),
    CHECK (
        (character_level IS NOT NULL AND source_tier IS NULL)
        OR (character_level IS NULL AND source_tier IS NOT NULL)
    ),
    UNIQUE (curve_id, character_level),
    UNIQUE (curve_id, source_tier)
);

CREATE TABLE reaction_definition (
    reaction_type TEXT PRIMARY KEY,
    element_type_1 TEXT NOT NULL,
    element_type_2 TEXT NOT NULL,
    default_damage_effect_id TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE combat_effect_constant (
    constant_id TEXT PRIMARY KEY,
    source_time REAL NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL CHECK (unit IN ('scalar', 'seconds', 'points', 'ratio')),
    description_zh TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE enemy_combat_profile (
    profile_set TEXT NOT NULL CHECK (profile_set IN ('standard', 'night_999')),
    pack_id TEXT NOT NULL,
    defense_base REAL NOT NULL,
    defense_up REAL NOT NULL,
    defense_add REAL NOT NULL,
    defense_ignore REAL NOT NULL,
    topple_limit REAL NOT NULL CHECK (topple_limit >= 0),
    topple_accrue_efficiency REAL NOT NULL,
    topple_anti_accrue_efficiency REAL NOT NULL,
    topple_bonus REAL NOT NULL,
    topple_reduce_natural REAL NOT NULL,
    topple_reduce_reset REAL NOT NULL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (profile_set, pack_id)
);

CREATE TABLE enemy_element_resistance (
    profile_set TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    damage_type TEXT NOT NULL,
    resistance_base REAL NOT NULL,
    immunity REAL NOT NULL,
    PRIMARY KEY (profile_set, pack_id, damage_type),
    FOREIGN KEY (profile_set, pack_id)
        REFERENCES enemy_combat_profile(profile_set, pack_id)
);

CREATE INDEX idx_combat_level_curve_kind
    ON combat_level_curve(damage_kind, reaction_type);
CREATE INDEX idx_enemy_combat_topple_limit
    ON enemy_combat_profile(profile_set, topple_limit);
