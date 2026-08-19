-- 静态库 v17：培养指南、技能文本、战斗标识、怪物别名与装备效果来源。

CREATE TABLE character_cultivation_guide (
    character_id INTEGER PRIMARY KEY REFERENCES character(character_id),
    display_text INTEGER NOT NULL CHECK (display_text IN (0, 1)),
    s_score REAL NOT NULL,
    a_score REAL NOT NULL,
    icon_path TEXT,
    recommend_attribute_jump_id TEXT,
    role_sex_change INTEGER NOT NULL CHECK (role_sex_change IN (0, 1)),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE character_cultivation_fork_recommendation (
    character_id INTEGER NOT NULL REFERENCES character_cultivation_guide(character_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    fork_id TEXT NOT NULL REFERENCES fork_item(fork_id),
    description_zh TEXT,
    source_kind TEXT,
    PRIMARY KEY (character_id, ordinal)
);

CREATE INDEX idx_cultivation_fork_recommendation_fork
    ON character_cultivation_fork_recommendation(fork_id, character_id);

CREATE TABLE character_cultivation_attribute_recommendation (
    character_id INTEGER NOT NULL REFERENCES character_cultivation_guide(character_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    property_id TEXT NOT NULL REFERENCES equipment_attribute(attribute_id),
    PRIMARY KEY (character_id, ordinal)
);

CREATE TABLE character_cultivation_stage (
    character_id INTEGER NOT NULL REFERENCES character_cultivation_guide(character_id),
    stage_ordinal INTEGER NOT NULL CHECK (stage_ordinal >= 0),
    character_level INTEGER NOT NULL CHECK (character_level > 0),
    fork_level INTEGER NOT NULL CHECK (fork_level > 0),
    core_item_id TEXT NOT NULL REFERENCES equipment_item(item_id),
    core_level INTEGER NOT NULL CHECK (core_level > 0),
    equipment_level INTEGER NOT NULL CHECK (equipment_level > 0),
    PRIMARY KEY (character_id, stage_ordinal)
);

CREATE TABLE character_cultivation_stage_skill (
    character_id INTEGER NOT NULL,
    stage_ordinal INTEGER NOT NULL,
    sex_kind TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    ability_id TEXT NOT NULL,
    recommended_level INTEGER NOT NULL CHECK (recommended_level > 0),
    PRIMARY KEY (character_id, stage_ordinal, sex_kind, ordinal),
    FOREIGN KEY (character_id, stage_ordinal)
        REFERENCES character_cultivation_stage(character_id, stage_ordinal)
);

CREATE INDEX idx_cultivation_stage_skill_ability
    ON character_cultivation_stage_skill(ability_id, character_id);

CREATE TABLE gameplay_ability_catalog (
    ability_id TEXT PRIMARY KEY,
    name_zh TEXT,
    name_text_table TEXT,
    name_text_key TEXT,
    icon_path TEXT,
    extended_icon_path TEXT,
    gameplay_ability_path TEXT,
    is_stolen INTEGER NOT NULL CHECK (is_stolen IN (0, 1)),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE gameplay_ability_description (
    ability_id TEXT NOT NULL REFERENCES gameplay_ability_catalog(ability_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    description_type TEXT,
    title_zh TEXT,
    description_zh TEXT,
    description_text_table TEXT,
    description_text_key TEXT,
    short_description_zh TEXT,
    unlock_id TEXT,
    unlock_description_zh TEXT,
    replacement_values_json TEXT NOT NULL CHECK (json_valid(replacement_values_json)),
    PRIMARY KEY (ability_id, ordinal)
);

CREATE TABLE gameplay_ability_level_hint (
    ability_id TEXT NOT NULL REFERENCES gameplay_ability_catalog(ability_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    name_id TEXT,
    description_zh TEXT,
    value_description_zh TEXT,
    global_curve_id TEXT,
    source_type TEXT,
    damage_effect_ids_json TEXT NOT NULL CHECK (json_valid(damage_effect_ids_json)),
    defense_effect_ids_json TEXT NOT NULL CHECK (json_valid(defense_effect_ids_json)),
    health_effect_ids_json TEXT NOT NULL CHECK (json_valid(health_effect_ids_json)),
    PRIMARY KEY (ability_id, ordinal)
);

CREATE TABLE gameplay_effect_catalog (
    gameplay_effect_index INTEGER PRIMARY KEY CHECK (gameplay_effect_index > 0),
    gameplay_effect_id TEXT NOT NULL UNIQUE,
    class_path TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE monster_catalog (
    monster_manual_id TEXT PRIMARY KEY,
    sort_order INTEGER NOT NULL,
    name_zh TEXT NOT NULL,
    enemy_type TEXT,
    image_path TEXT,
    world_image_path TEXT,
    place_zh TEXT,
    discovered_description_zh TEXT,
    undiscovered_description_zh TEXT,
    drop_id TEXT,
    stamina_cost INTEGER,
    trace_type TEXT,
    map_icon_id TEXT,
    quest_id TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE monster_identifier_alias (
    monster_manual_id TEXT NOT NULL REFERENCES monster_catalog(monster_manual_id),
    alias_kind TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    alias_value TEXT NOT NULL,
    PRIMARY KEY (monster_manual_id, alias_kind, ordinal)
);

CREATE INDEX idx_monster_identifier_alias_lookup
    ON monster_identifier_alias(alias_kind, alias_value);

CREATE TABLE equipment_modify_pack (
    modify_pack_id TEXT PRIMARY KEY,
    conditions_json TEXT NOT NULL CHECK (json_valid(conditions_json)),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE equipment_modify_value (
    modify_pack_id TEXT NOT NULL REFERENCES equipment_modify_pack(modify_pack_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    property_id TEXT NOT NULL,
    value REAL NOT NULL,
    operation TEXT NOT NULL,
    sort_key INTEGER,
    PRIMARY KEY (modify_pack_id, ordinal)
);

CREATE TABLE equipment_buff_curve (
    curve_id TEXT PRIMARY KEY,
    interpolation_mode TEXT,
    default_value REAL,
    pre_infinity_extrapolation TEXT,
    post_infinity_extrapolation TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE equipment_buff_curve_point (
    curve_id TEXT NOT NULL REFERENCES equipment_buff_curve(curve_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    source_time REAL NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (curve_id, ordinal)
);

CREATE TABLE combat_effect_definition (
    effect_definition_id TEXT PRIMARY KEY,
    owner_kind TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    effect_kind TEXT NOT NULL,
    activation_kind TEXT NOT NULL,
    description_zh TEXT,
    parameters_json TEXT NOT NULL CHECK (json_valid(parameters_json)),
    formula_version INTEGER NOT NULL CHECK (formula_version > 0),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE INDEX idx_combat_effect_definition_owner
    ON combat_effect_definition(owner_kind, owner_id);
