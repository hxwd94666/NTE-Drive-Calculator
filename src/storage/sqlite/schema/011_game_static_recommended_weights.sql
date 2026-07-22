-- 静态库 v11：开发期同步的角色推荐词条权重；运行时只读。

CREATE TABLE character_weight_recommendation (
    character_id INTEGER PRIMARY KEY REFERENCES character(character_id),
    source_kind TEXT NOT NULL CHECK (source_kind IN ('workshop_api', 'workshop_cache', 'default')),
    source_item_id TEXT,
    source_name TEXT,
    source_updated_at_utc TEXT NOT NULL
);

CREATE TABLE character_weight_recommendation_property (
    character_id INTEGER NOT NULL
        REFERENCES character_weight_recommendation(character_id) ON DELETE CASCADE,
    property_id TEXT NOT NULL REFERENCES equipment_attribute(attribute_id),
    weight REAL NOT NULL DEFAULT 0 CHECK (weight >= 0),
    main_weight REAL NOT NULL DEFAULT 0 CHECK (main_weight >= 0),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (character_id, property_id),
    UNIQUE (character_id, ordinal)
);

CREATE INDEX idx_character_weight_recommendation_source
    ON character_weight_recommendation(source_kind, character_id);
