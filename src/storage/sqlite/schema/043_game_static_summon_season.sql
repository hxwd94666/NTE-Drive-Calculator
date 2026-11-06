-- 静态库 v43：召唤伤害赛季是逐击标签限定，不提升为全队通用增伤。
ALTER TABLE outer_realm_season_buff_component RENAME TO outer_realm_season_buff_component_v37;
CREATE TABLE outer_realm_season_buff_component (
    level_config_id TEXT NOT NULL REFERENCES outer_realm_season_buff(level_config_id) ON DELETE CASCADE,
    component_ordinal INTEGER NOT NULL CHECK (component_ordinal >= 0),
    trigger_kind TEXT NOT NULL CHECK (trigger_kind IN (
        'whole_battle', 'corruption_damage_stack', 'while_target_toppled',
        'whole_battle_qte', 'observed_recipient_effect', 'whole_battle_summon')),
    property_id TEXT NOT NULL,
    property_value REAL NOT NULL,
    duration_seconds REAL CHECK (duration_seconds IS NULL OR duration_seconds > 0),
    trigger_cooldown_seconds REAL CHECK (trigger_cooldown_seconds IS NULL OR trigger_cooldown_seconds >= 0),
    stack_limit_count INTEGER NOT NULL DEFAULT 1 CHECK (stack_limit_count >= 1),
    curve_id TEXT NOT NULL,
    curve_source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    effect_asset_path TEXT,
    PRIMARY KEY (level_config_id, component_ordinal)
);
INSERT INTO outer_realm_season_buff_component SELECT * FROM outer_realm_season_buff_component_v37;
DROP TABLE outer_realm_season_buff_component_v37;
CREATE INDEX idx_outer_realm_buff_component_trigger
    ON outer_realm_season_buff_component(level_config_id, trigger_kind);
