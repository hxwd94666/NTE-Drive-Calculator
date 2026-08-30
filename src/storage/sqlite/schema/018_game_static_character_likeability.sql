-- 静态库 v18：角色好感度等级加成及其正式属性修改。

CREATE TABLE character_likeability_bonus (
    character_id INTEGER PRIMARY KEY REFERENCES character(character_id),
    required_level INTEGER NOT NULL CHECK (required_level > 0),
    modify_data_id TEXT NOT NULL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    modifier_source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE character_likeability_bonus_property (
    character_id INTEGER NOT NULL
        REFERENCES character_likeability_bonus(character_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    property_id TEXT NOT NULL REFERENCES equipment_attribute(attribute_id),
    value REAL NOT NULL,
    modifier_operation TEXT NOT NULL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (character_id, ordinal),
    UNIQUE (character_id, property_id)
);

CREATE INDEX idx_character_likeability_bonus_property
    ON character_likeability_bonus_property(property_id, character_id);
