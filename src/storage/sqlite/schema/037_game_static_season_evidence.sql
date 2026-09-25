-- 静态库 v37：赛季限定标签、受益对象证据和显式补充排期。
CREATE TABLE outer_realm_rotation_annotation (
    level_config_id TEXT PRIMARY KEY REFERENCES outer_realm_rotation(level_config_id),
    basis TEXT NOT NULL,
    predecessor_id TEXT NOT NULL,
    successor_id TEXT NOT NULL
);

ALTER TABLE outer_realm_season_buff_component RENAME TO outer_realm_season_buff_component_v26;
CREATE TABLE outer_realm_season_buff_component (
    level_config_id TEXT NOT NULL REFERENCES outer_realm_season_buff(level_config_id) ON DELETE CASCADE,
    component_ordinal INTEGER NOT NULL CHECK (component_ordinal >= 0),
    trigger_kind TEXT NOT NULL CHECK (trigger_kind IN (
        'whole_battle', 'corruption_damage_stack', 'while_target_toppled',
        'whole_battle_qte', 'observed_recipient_effect')),
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
INSERT INTO outer_realm_season_buff_component
SELECT *, NULL FROM outer_realm_season_buff_component_v26;
DROP TABLE outer_realm_season_buff_component_v26;
CREATE INDEX idx_outer_realm_buff_component_trigger
    ON outer_realm_season_buff_component(level_config_id, trigger_kind);
