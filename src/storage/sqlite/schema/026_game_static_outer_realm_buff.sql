-- 静态库 v26：轨外之境当前/下一期赛季 Buff 及可计算组成。

CREATE TABLE outer_realm_season_buff (
    level_config_id TEXT PRIMARY KEY,
    season_name_zh TEXT NOT NULL,
    buff_id TEXT NOT NULL UNIQUE,
    buff_name_zh TEXT NOT NULL,
    description_zh TEXT NOT NULL,
    gameplay_effect_path TEXT NOT NULL,
    add_to_character INTEGER NOT NULL CHECK (add_to_character IN (0, 1)),
    season_source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    buff_source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    FOREIGN KEY (level_config_id) REFERENCES outer_realm_rotation(level_config_id)
);

CREATE TABLE outer_realm_season_buff_component (
    level_config_id TEXT NOT NULL
        REFERENCES outer_realm_season_buff(level_config_id) ON DELETE CASCADE,
    component_ordinal INTEGER NOT NULL CHECK (component_ordinal >= 0),
    trigger_kind TEXT NOT NULL CHECK (
        trigger_kind IN (
            'whole_battle',
            'corruption_damage_stack',
            'while_target_toppled'
        )
    ),
    property_id TEXT NOT NULL,
    property_value REAL NOT NULL,
    duration_seconds REAL CHECK (duration_seconds IS NULL OR duration_seconds > 0),
    trigger_cooldown_seconds REAL
        CHECK (trigger_cooldown_seconds IS NULL OR trigger_cooldown_seconds >= 0),
    stack_limit_count INTEGER NOT NULL DEFAULT 1 CHECK (stack_limit_count >= 1),
    curve_id TEXT NOT NULL,
    curve_source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (level_config_id, component_ordinal)
);

CREATE INDEX idx_outer_realm_buff_component_trigger
    ON outer_realm_season_buff_component(level_config_id, trigger_kind);
