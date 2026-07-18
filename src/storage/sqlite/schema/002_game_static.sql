PRAGMA foreign_keys = ON;

CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY,
    applied_at_utc TEXT NOT NULL
);

CREATE TABLE dataset (
    dataset_id TEXT PRIMARY KEY,
    game_version TEXT,
    importer_version INTEGER NOT NULL,
    built_at_utc TEXT NOT NULL
);

CREATE TABLE source_file (
    source_file_id INTEGER PRIMARY KEY,
    relative_path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,
    row_count INTEGER NOT NULL CHECK (row_count >= 0)
);

CREATE TABLE source_row (
    source_row_id INTEGER PRIMARY KEY,
    source_file_id INTEGER NOT NULL REFERENCES source_file(source_file_id),
    row_key TEXT NOT NULL,
    payload_json TEXT,
    content_sha256 TEXT NOT NULL,
    UNIQUE (source_file_id, row_key)
);

CREATE TABLE character (
    character_id INTEGER PRIMARY KEY,
    name_zh TEXT NOT NULL,
    name_text_table TEXT,
    name_text_key TEXT,
    element_type TEXT,
    group_type TEXT,
    actor_path TEXT,
    mainland_show_time TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE character_annotation (
    character_id INTEGER PRIMARY KEY REFERENCES character(character_id),
    logical_character_key TEXT NOT NULL,
    canonical_character_id INTEGER REFERENCES character(character_id),
    classification TEXT NOT NULL,
    annotation_source TEXT NOT NULL
);

CREATE TABLE equipment_attribute (
    attribute_id TEXT PRIMARY KEY,
    display_name_zh TEXT,
    filter_name_zh TEXT,
    random_attribute_name_zh TEXT,
    attribute_type TEXT,
    show_percent INTEGER NOT NULL CHECK (show_percent IN (0, 1)),
    show_outside INTEGER NOT NULL CHECK (show_outside IN (0, 1)),
    show_inside INTEGER NOT NULL CHECK (show_inside IN (0, 1)),
    score REAL,
    icon_path TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE equipment_shape (
    shape_id TEXT PRIMARY KEY,
    cell_count INTEGER NOT NULL CHECK (cell_count > 0),
    first_grid_delta_x INTEGER NOT NULL,
    first_grid_delta_y INTEGER NOT NULL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE equipment_shape_cell (
    shape_id TEXT NOT NULL REFERENCES equipment_shape(shape_id),
    ordinal INTEGER NOT NULL,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    PRIMARY KEY (shape_id, ordinal),
    UNIQUE (shape_id, x, y)
);

CREATE TABLE equipment_suit (
    suit_id TEXT PRIMARY KEY,
    name_zh TEXT NOT NULL,
    name_text_table TEXT,
    name_text_key TEXT,
    icon_path TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE equipment_suit_required_shape (
    suit_id TEXT NOT NULL REFERENCES equipment_suit(suit_id),
    ordinal INTEGER NOT NULL,
    shape_id TEXT NOT NULL REFERENCES equipment_shape(shape_id),
    PRIMARY KEY (suit_id, ordinal),
    UNIQUE (suit_id, shape_id)
);

CREATE TABLE equipment_suit_effect (
    suit_id TEXT NOT NULL REFERENCES equipment_suit(suit_id),
    required_count INTEGER NOT NULL CHECK (required_count > 0),
    modify_pack_id TEXT,
    buff_object_path TEXT,
    description_zh TEXT,
    description_text_table TEXT,
    description_text_key TEXT,
    reapply_after_revive INTEGER NOT NULL CHECK (reapply_after_revive IN (0, 1)),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (suit_id, required_count)
);

CREATE TABLE equipment_item (
    item_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('module', 'core')),
    quality TEXT NOT NULL,
    name_zh TEXT NOT NULL,
    name_text_table TEXT,
    name_text_key TEXT,
    geometry_id TEXT REFERENCES equipment_shape(shape_id),
    geometry_enum TEXT,
    grid_count INTEGER,
    suit_id TEXT REFERENCES equipment_suit(suit_id),
    suit_type_enum TEXT,
    max_level INTEGER NOT NULL,
    random_base_attribute_pool_id TEXT,
    random_base_attribute_count INTEGER NOT NULL,
    random_sub_attribute_pool_id TEXT,
    random_sub_attribute_count INTEGER NOT NULL,
    random_sub_attribute_max_count INTEGER NOT NULL,
    strength_pack_id TEXT,
    icon_path TEXT,
    plan_icon_path TEXT,
    is_guide_item INTEGER NOT NULL CHECK (is_guide_item IN (0, 1)),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE equipment_strength_level (
    strength_pack_id TEXT NOT NULL,
    level INTEGER NOT NULL CHECK (level > 0),
    need_exp INTEGER NOT NULL CHECK (need_exp >= 0),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (strength_pack_id, level)
);

CREATE TABLE equipment_base_attribute_curve (
    curve_id TEXT PRIMARY KEY,
    interpolation_mode TEXT,
    pre_infinity_extrapolation TEXT,
    post_infinity_extrapolation TEXT,
    default_value REAL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE equipment_base_attribute_point (
    curve_id TEXT NOT NULL REFERENCES equipment_base_attribute_curve(curve_id),
    ordinal INTEGER NOT NULL,
    level REAL NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (curve_id, ordinal),
    UNIQUE (curve_id, level)
);

CREATE TABLE equipment_core_random_attribute (
    attribute_id TEXT PRIMARY KEY,
    content_zh TEXT,
    content_text_table TEXT,
    content_text_key TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE equipment_plan (
    character_id INTEGER PRIMARY KEY REFERENCES character(character_id),
    core_item_id TEXT NOT NULL REFERENCES equipment_item(item_id),
    core_level INTEGER NOT NULL,
    module_level INTEGER NOT NULL,
    reference_score REAL NOT NULL,
    background_path TEXT,
    character_image_path TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE equipment_plan_core_attribute (
    character_id INTEGER NOT NULL REFERENCES equipment_plan(character_id),
    ordinal INTEGER NOT NULL,
    attribute_id TEXT NOT NULL REFERENCES equipment_attribute(attribute_id),
    PRIMARY KEY (character_id, ordinal),
    UNIQUE (character_id, attribute_id)
);

CREATE TABLE equipment_plan_recommended_attribute (
    character_id INTEGER NOT NULL REFERENCES equipment_plan(character_id),
    ordinal INTEGER NOT NULL,
    attribute_id TEXT NOT NULL REFERENCES equipment_attribute(attribute_id),
    PRIMARY KEY (character_id, ordinal),
    UNIQUE (character_id, attribute_id)
);

CREATE TABLE equipment_plan_cell (
    character_id INTEGER NOT NULL REFERENCES equipment_plan(character_id),
    row INTEGER NOT NULL CHECK (row BETWEEN 1 AND 5),
    column INTEGER NOT NULL CHECK (column BETWEEN 1 AND 5),
    anchor_item_id TEXT REFERENCES equipment_item(item_id),
    PRIMARY KEY (character_id, row, column)
);

CREATE TABLE equipment_plan_module (
    character_id INTEGER NOT NULL REFERENCES equipment_plan(character_id),
    ordinal INTEGER NOT NULL,
    item_id TEXT NOT NULL REFERENCES equipment_item(item_id),
    PRIMARY KEY (character_id, ordinal)
);

CREATE TABLE fork_type (
    fork_type_id INTEGER PRIMARY KEY,
    name_zh TEXT NOT NULL,
    description_zh TEXT,
    icon_path TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE fork_item (
    fork_id TEXT PRIMARY KEY,
    name_zh TEXT NOT NULL,
    name_text_table TEXT,
    name_text_key TEXT,
    description_zh TEXT,
    quality TEXT NOT NULL,
    fork_type_id INTEGER REFERENCES fork_type(fork_type_id),
    raw_group_type TEXT,
    upgrade_pack_id TEXT,
    breakthrough_pack_id TEXT,
    star_pack_id TEXT,
    max_breakthrough INTEGER,
    max_star INTEGER,
    icon_path TEXT,
    card_path TEXT,
    painting_path TEXT,
    exclusive_character_ids_json TEXT NOT NULL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE fork_upgrade_level (
    upgrade_pack_id TEXT NOT NULL,
    level INTEGER NOT NULL CHECK (level > 0),
    need_exp INTEGER NOT NULL CHECK (need_exp >= 0),
    modify_pack_id TEXT NOT NULL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (upgrade_pack_id, level)
);

CREATE TABLE fork_modify_pack (
    modify_pack_id TEXT PRIMARY KEY,
    conditions_json TEXT NOT NULL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE fork_modify_value (
    modify_pack_id TEXT NOT NULL REFERENCES fork_modify_pack(modify_pack_id),
    ordinal INTEGER NOT NULL,
    property_id TEXT NOT NULL,
    value REAL NOT NULL,
    operation TEXT NOT NULL,
    sort_key INTEGER,
    PRIMARY KEY (modify_pack_id, ordinal)
);

CREATE TABLE fork_breakthrough (
    breakthrough_pack_id TEXT NOT NULL,
    stage INTEGER NOT NULL CHECK (stage >= 0),
    max_fork_level INTEGER NOT NULL,
    need_items TEXT,
    need_gold TEXT,
    modify_pack_id TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (breakthrough_pack_id, stage)
);

CREATE TABLE fork_star_level (
    star_pack_id TEXT NOT NULL,
    star_level INTEGER NOT NULL CHECK (star_level > 0),
    title_zh TEXT,
    description_zh TEXT,
    need_gold TEXT,
    buffs_json TEXT NOT NULL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (star_pack_id, star_level)
);

CREATE TABLE fork_star_parameter (
    star_pack_id TEXT NOT NULL,
    star_level INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    name_id TEXT NOT NULL,
    is_percent INTEGER NOT NULL CHECK (is_percent IN (0, 1)),
    PRIMARY KEY (star_pack_id, star_level, ordinal),
    FOREIGN KEY (star_pack_id, star_level)
        REFERENCES fork_star_level(star_pack_id, star_level)
);

CREATE INDEX idx_source_row_file ON source_row(source_file_id);
CREATE INDEX idx_character_annotation_logical_key
    ON character_annotation(logical_character_key);
CREATE INDEX idx_equipment_item_suit ON equipment_item(suit_id);
CREATE INDEX idx_equipment_item_geometry ON equipment_item(geometry_id);
CREATE INDEX idx_fork_item_type ON fork_item(fork_type_id);
