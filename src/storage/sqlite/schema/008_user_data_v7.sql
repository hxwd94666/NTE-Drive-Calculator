-- 用户数据库 v7：从只读静态库复制、随后由账号独立编辑的角色词条权重。

CREATE TABLE character_weight_preference_seed (
    character_id INTEGER PRIMARY KEY CHECK (character_id > 0),
    source_dataset_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    seeded_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE character_weight_preference_property (
    character_id INTEGER NOT NULL
        REFERENCES character_weight_preference_seed(character_id) ON DELETE CASCADE,
    property_id TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 0 CHECK (weight >= 0),
    main_weight REAL NOT NULL DEFAULT 0 CHECK (main_weight >= 0),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (character_id, property_id),
    UNIQUE (character_id, ordinal)
);

CREATE INDEX idx_character_weight_preference_property_character
    ON character_weight_preference_property(character_id, ordinal);
