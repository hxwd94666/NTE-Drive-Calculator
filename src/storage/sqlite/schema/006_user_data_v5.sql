-- 用户数据库 v5：版本化词条权重配装偏好。
-- 偏好只保存账号自己的优化目标；官方静态 ID 保持在列中，显示名由静态库解析。

CREATE TABLE optimization_preference_profile (
    profile_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE optimization_preference_version (
    profile_version_id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL
        REFERENCES optimization_preference_profile(profile_id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL CHECK (version_number >= 1),
    allocation_strategy TEXT NOT NULL
        CHECK (allocation_strategy IN ('role_priority', 'drive_priority', 'global_optimal')),
    created_at_utc TEXT NOT NULL,
    UNIQUE (profile_id, version_number)
);

CREATE INDEX idx_optimization_preference_version_profile
    ON optimization_preference_version(profile_id, version_number DESC);

CREATE TABLE optimization_preference_character (
    profile_version_id INTEGER NOT NULL
        REFERENCES optimization_preference_version(profile_version_id) ON DELETE CASCADE,
    character_id INTEGER NOT NULL CHECK (character_id > 0),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    priority_group INTEGER NOT NULL DEFAULT 0 CHECK (priority_group >= 0),
    target_suit_id TEXT,
    suit_requirement_mode TEXT NOT NULL DEFAULT 'none'
        CHECK (suit_requirement_mode IN ('none', 'two_piece', 'four_piece')),
    core_main_property_id TEXT,
    CHECK (
        suit_requirement_mode = 'none'
        OR coalesce(length(trim(target_suit_id)), 0) > 0
    ),
    PRIMARY KEY (profile_version_id, character_id),
    UNIQUE (profile_version_id, ordinal)
);

CREATE INDEX idx_optimization_preference_character_order
    ON optimization_preference_character(profile_version_id, priority_group, ordinal);

CREATE TABLE optimization_preference_property_weight (
    profile_version_id INTEGER NOT NULL,
    character_id INTEGER NOT NULL,
    property_id TEXT NOT NULL,
    weight REAL NOT NULL,
    PRIMARY KEY (profile_version_id, character_id, property_id),
    FOREIGN KEY (profile_version_id, character_id)
        REFERENCES optimization_preference_character(profile_version_id, character_id)
        ON DELETE CASCADE
);

CREATE TABLE optimization_preference_substat_priority (
    profile_version_id INTEGER NOT NULL,
    character_id INTEGER NOT NULL,
    property_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (profile_version_id, character_id, property_id),
    UNIQUE (profile_version_id, character_id, ordinal),
    FOREIGN KEY (profile_version_id, character_id)
        REFERENCES optimization_preference_character(profile_version_id, character_id)
        ON DELETE CASCADE
);

CREATE TABLE optimization_preference_property_limit (
    profile_version_id INTEGER NOT NULL,
    character_id INTEGER NOT NULL,
    property_id TEXT NOT NULL,
    minimum_value REAL,
    maximum_value REAL,
    PRIMARY KEY (profile_version_id, character_id, property_id),
    CHECK (
        minimum_value IS NOT NULL OR maximum_value IS NOT NULL
    ),
    CHECK (
        minimum_value IS NULL OR maximum_value IS NULL OR minimum_value <= maximum_value
    ),
    FOREIGN KEY (profile_version_id, character_id)
        REFERENCES optimization_preference_character(profile_version_id, character_id)
        ON DELETE CASCADE
);
