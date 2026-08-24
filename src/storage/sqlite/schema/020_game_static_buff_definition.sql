-- 静态库 v20：敌人生命/RogueLike 修正与规范化 Buff 定义。

ALTER TABLE enemy_combat_profile
    ADD COLUMN health_base REAL NOT NULL DEFAULT 0 CHECK (health_base >= 0);

ALTER TABLE enemy_combat_profile
    ADD COLUMN health_up REAL NOT NULL DEFAULT 0;

ALTER TABLE enemy_combat_profile
    ADD COLUMN health_add REAL NOT NULL DEFAULT 0;

CREATE TABLE roguelike_modifier_profile (
    modifier_id TEXT PRIMARY KEY,
    conditions_json TEXT NOT NULL CHECK (json_valid(conditions_json)),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE roguelike_modifier_property (
    modifier_id TEXT NOT NULL REFERENCES roguelike_modifier_profile(modifier_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    property_id TEXT NOT NULL CHECK (length(trim(property_id)) > 0),
    modifier_operation TEXT NOT NULL,
    property_value REAL NOT NULL,
    sort_key INTEGER NOT NULL,
    PRIMARY KEY (modifier_id, ordinal)
);

CREATE INDEX idx_roguelike_modifier_property_id
    ON roguelike_modifier_property(property_id, modifier_id);

CREATE TABLE combat_curve (
    curve_table_asset_path TEXT NOT NULL COLLATE NOCASE,
    curve_id TEXT NOT NULL,
    interpolation_mode TEXT,
    default_value REAL,
    pre_infinity_extrapolation TEXT,
    post_infinity_extrapolation TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (curve_table_asset_path, curve_id)
);

CREATE TABLE combat_curve_point (
    curve_table_asset_path TEXT NOT NULL COLLATE NOCASE,
    curve_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    source_time REAL NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (curve_table_asset_path, curve_id, ordinal),
    FOREIGN KEY (curve_table_asset_path, curve_id)
        REFERENCES combat_curve(curve_table_asset_path, curve_id)
);

CREATE INDEX idx_combat_curve_id
    ON combat_curve(curve_id, curve_table_asset_path);

CREATE TABLE buff_definition (
    asset_path TEXT PRIMARY KEY COLLATE NOCASE
        REFERENCES combat_blueprint_asset(asset_path),
    definition_id TEXT NOT NULL,
    definition_kind TEXT NOT NULL
        CHECK (definition_kind IN ('buff', 'gameplay_effect')),
    owner_character_id INTEGER REFERENCES character(character_id),
    duration_policy TEXT,
    duration_magnitude_json TEXT CHECK (
        duration_magnitude_json IS NULL OR json_valid(duration_magnitude_json)
    ),
    period_json TEXT CHECK (period_json IS NULL OR json_valid(period_json)),
    stacking_type TEXT,
    stack_limit_count INTEGER,
    source_file_id INTEGER NOT NULL REFERENCES source_file(source_file_id)
);

CREATE INDEX idx_buff_definition_id
    ON buff_definition(definition_id, definition_kind);
CREATE INDEX idx_buff_definition_character
    ON buff_definition(owner_character_id, definition_kind);

CREATE TABLE buff_modifier (
    asset_path TEXT NOT NULL COLLATE NOCASE
        REFERENCES buff_definition(asset_path),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    property_id TEXT,
    modifier_operation TEXT,
    magnitude_kind TEXT,
    magnitude_value REAL,
    calculation_asset_path TEXT COLLATE NOCASE,
    magnitude_json TEXT NOT NULL CHECK (json_valid(magnitude_json)),
    source_property_path TEXT NOT NULL,
    PRIMARY KEY (asset_path, ordinal)
);

CREATE INDEX idx_buff_modifier_property
    ON buff_modifier(property_id, asset_path);
CREATE INDEX idx_buff_modifier_calculation
    ON buff_modifier(calculation_asset_path, asset_path);

CREATE TABLE buff_trigger_effect (
    asset_path TEXT NOT NULL COLLATE NOCASE
        REFERENCES buff_definition(asset_path),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    event_type TEXT NOT NULL,
    effect_type TEXT NOT NULL,
    target_effect_asset_path TEXT NOT NULL COLLATE NOCASE,
    stack_count INTEGER,
    by_self INTEGER NOT NULL CHECK (by_self IN (0, 1)),
    target_trigger INTEGER NOT NULL CHECK (target_trigger IN (0, 1)),
    modify_duration_json TEXT CHECK (
        modify_duration_json IS NULL OR json_valid(modify_duration_json)
    ),
    application_requirement_asset_path TEXT COLLATE NOCASE,
    PRIMARY KEY (asset_path, ordinal)
);

CREATE INDEX idx_buff_trigger_event
    ON buff_trigger_effect(event_type, asset_path);
CREATE INDEX idx_buff_trigger_target
    ON buff_trigger_effect(target_effect_asset_path, asset_path);

CREATE TABLE combat_effect_buff_link (
    effect_definition_id TEXT NOT NULL
        REFERENCES combat_effect_definition(effect_definition_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    link_kind TEXT NOT NULL
        CHECK (link_kind IN ('buff_object', 'fork_buff', 'gameplay_effect')),
    target_asset_path TEXT NOT NULL COLLATE NOCASE,
    target_available INTEGER NOT NULL CHECK (target_available IN (0, 1)),
    PRIMARY KEY (effect_definition_id, ordinal)
);

CREATE INDEX idx_combat_effect_buff_target
    ON combat_effect_buff_link(target_asset_path, effect_definition_id);
