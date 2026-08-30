CREATE TABLE character_progression_profile (
    character_id INTEGER PRIMARY KEY REFERENCES character(character_id),
    upgrade_pack_id TEXT NOT NULL,
    breakthrough_pack_id TEXT NOT NULL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE character_upgrade_level (
    upgrade_pack_id TEXT NOT NULL,
    level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 80),
    need_exp INTEGER NOT NULL CHECK (need_exp > 0),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (upgrade_pack_id, level)
);

CREATE TABLE character_breakthrough_stage (
    breakthrough_pack_id TEXT NOT NULL,
    stage INTEGER NOT NULL CHECK (stage BETWEEN 0 AND 6),
    max_character_level INTEGER NOT NULL CHECK (max_character_level BETWEEN 1 AND 80),
    required_world_level INTEGER NOT NULL CHECK (required_world_level >= 0),
    modify_pack_id TEXT NOT NULL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (breakthrough_pack_id, stage),
    UNIQUE (breakthrough_pack_id, max_character_level)
);

CREATE TABLE character_breakthrough_cost (
    breakthrough_pack_id TEXT NOT NULL,
    stage INTEGER NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    item_id TEXT NOT NULL REFERENCES progression_item(item_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (breakthrough_pack_id, stage, ordinal),
    UNIQUE (breakthrough_pack_id, stage, item_id),
    FOREIGN KEY (breakthrough_pack_id, stage)
        REFERENCES character_breakthrough_stage(breakthrough_pack_id, stage)
);

CREATE TABLE character_exp_material (
    item_id TEXT PRIMARY KEY REFERENCES progression_item(item_id),
    experience_value INTEGER NOT NULL CHECK (experience_value > 0),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE character_exp_material_cost (
    item_id TEXT NOT NULL REFERENCES character_exp_material(item_id),
    cost_item_id TEXT NOT NULL REFERENCES progression_item(item_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (item_id, cost_item_id)
);
