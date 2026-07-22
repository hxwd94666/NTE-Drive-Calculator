-- 静态库 v9：怪物实例与明确属性包变体的可追溯绑定。

CREATE TABLE monster_instance_profile (
    static_table TEXT NOT NULL,
    monster_id TEXT NOT NULL,
    monster_level INTEGER NOT NULL CHECK (monster_level >= 0),
    default_profile_set TEXT NOT NULL CHECK (default_profile_set IN ('standard', 'night_999')),
    default_pack_id TEXT,
    online_ratio_id TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (static_table, monster_id)
);

CREATE TABLE monster_instance_profile_variant (
    static_table TEXT NOT NULL,
    monster_id TEXT NOT NULL,
    variant_kind TEXT NOT NULL CHECK (variant_kind IN ('world_level', 'clone_level', 'abyss_level')),
    threshold_level INTEGER NOT NULL CHECK (threshold_level >= 0),
    profile_set TEXT NOT NULL CHECK (profile_set IN ('standard', 'night_999')),
    pack_id TEXT NOT NULL,
    PRIMARY KEY (static_table, monster_id, variant_kind, threshold_level),
    FOREIGN KEY (static_table, monster_id)
        REFERENCES monster_instance_profile(static_table, monster_id)
);

CREATE INDEX idx_monster_instance_profile_pack
    ON monster_instance_profile(default_profile_set, default_pack_id);
