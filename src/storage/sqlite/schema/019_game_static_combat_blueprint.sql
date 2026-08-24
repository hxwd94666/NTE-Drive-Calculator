-- 静态库 v19：角色战斗 Blueprint、输入绑定、技能效果与动画时间证据。

CREATE TABLE combat_blueprint_asset (
    asset_path TEXT PRIMARY KEY COLLATE NOCASE,
    asset_name TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    asset_kind TEXT NOT NULL CHECK (
        asset_kind IN (
            'character', 'ability', 'gameplay_effect', 'buff',
            'calculation', 'condition', 'montage', 'animation', 'other'
        )
    ),
    character_id INTEGER REFERENCES character(character_id),
    source_file_id INTEGER NOT NULL REFERENCES source_file(source_file_id)
);

CREATE INDEX idx_combat_blueprint_asset_character
    ON combat_blueprint_asset(character_id, asset_kind);
CREATE INDEX idx_combat_blueprint_asset_name
    ON combat_blueprint_asset(asset_name);

CREATE TABLE character_combat_ability_binding (
    character_id INTEGER NOT NULL REFERENCES character(character_id),
    binding_kind TEXT NOT NULL CHECK (
        binding_kind IN ('active', 'passive', 'passive_buff')
    ),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    input_id TEXT,
    ability_id TEXT NOT NULL,
    ability_asset_path TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (character_id, binding_kind, ordinal)
);

CREATE INDEX idx_character_combat_ability_id
    ON character_combat_ability_binding(ability_id, character_id);
CREATE INDEX idx_character_combat_ability_path
    ON character_combat_ability_binding(ability_asset_path);

CREATE TABLE combat_blueprint_reference (
    source_asset_path TEXT NOT NULL COLLATE NOCASE
        REFERENCES combat_blueprint_asset(asset_path),
    property_path TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    relation_kind TEXT NOT NULL,
    target_asset_path TEXT NOT NULL COLLATE NOCASE,
    target_object_path TEXT NOT NULL,
    target_object_name TEXT,
    target_available INTEGER NOT NULL CHECK (target_available IN (0, 1)),
    PRIMARY KEY (source_asset_path, property_path, ordinal)
);

CREATE INDEX idx_combat_blueprint_reference_target
    ON combat_blueprint_reference(target_asset_path, relation_kind);

CREATE TABLE combat_blueprint_tag (
    source_asset_path TEXT NOT NULL COLLATE NOCASE
        REFERENCES combat_blueprint_asset(asset_path),
    property_path TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    tag_name TEXT NOT NULL,
    PRIMARY KEY (source_asset_path, property_path, ordinal)
);

CREATE INDEX idx_combat_blueprint_tag_name
    ON combat_blueprint_tag(tag_name, source_asset_path);

CREATE TABLE combat_blueprint_semantic_property (
    source_asset_path TEXT NOT NULL COLLATE NOCASE
        REFERENCES combat_blueprint_asset(asset_path),
    property_path TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    property_name TEXT NOT NULL,
    value_json TEXT NOT NULL CHECK (json_valid(value_json)),
    PRIMARY KEY (source_asset_path, property_path, ordinal)
);

CREATE INDEX idx_combat_blueprint_semantic_property_name
    ON combat_blueprint_semantic_property(property_name, source_asset_path);

CREATE TABLE combat_ability_montage_binding (
    ability_asset_path TEXT NOT NULL COLLATE NOCASE
        REFERENCES combat_blueprint_asset(asset_path),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    selector_key TEXT NOT NULL,
    montage_asset_path TEXT NOT NULL COLLATE NOCASE,
    montage_object_path TEXT NOT NULL,
    PRIMARY KEY (ability_asset_path, ordinal)
);

CREATE INDEX idx_combat_ability_montage_target
    ON combat_ability_montage_binding(montage_asset_path);

CREATE TABLE combat_ability_effect_binding (
    ability_asset_path TEXT NOT NULL COLLATE NOCASE
        REFERENCES combat_blueprint_asset(asset_path),
    event_tag TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    effect_asset_path TEXT NOT NULL COLLATE NOCASE,
    effect_id TEXT NOT NULL,
    target_type_asset_path TEXT COLLATE NOCASE,
    PRIMARY KEY (ability_asset_path, event_tag, ordinal)
);

CREATE INDEX idx_combat_ability_effect_target
    ON combat_ability_effect_binding(effect_asset_path, ability_asset_path);

CREATE TABLE combat_montage (
    asset_path TEXT PRIMARY KEY COLLATE NOCASE REFERENCES combat_blueprint_asset(asset_path),
    duration_seconds REAL NOT NULL CHECK (duration_seconds >= 0),
    blend_in_seconds REAL,
    blend_out_seconds REAL,
    frame_rate_numerator INTEGER,
    frame_rate_denominator INTEGER
);

CREATE TABLE combat_montage_section (
    asset_path TEXT NOT NULL COLLATE NOCASE REFERENCES combat_montage(asset_path),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    section_name TEXT NOT NULL,
    next_section_name TEXT,
    start_seconds REAL NOT NULL CHECK (start_seconds >= 0),
    end_seconds REAL NOT NULL CHECK (end_seconds >= start_seconds),
    linked_animation_asset_path TEXT COLLATE NOCASE,
    PRIMARY KEY (asset_path, ordinal)
);

CREATE TABLE combat_montage_notify (
    asset_path TEXT NOT NULL COLLATE NOCASE REFERENCES combat_montage(asset_path),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    notify_name TEXT NOT NULL,
    notify_object_path TEXT,
    start_seconds REAL NOT NULL,
    end_seconds REAL NOT NULL CHECK (end_seconds >= start_seconds),
    event_tag TEXT,
    track_index INTEGER,
    PRIMARY KEY (asset_path, ordinal)
);

CREATE INDEX idx_combat_montage_notify_name
    ON combat_montage_notify(notify_name, asset_path);
CREATE INDEX idx_combat_montage_notify_event
    ON combat_montage_notify(event_tag, asset_path);
