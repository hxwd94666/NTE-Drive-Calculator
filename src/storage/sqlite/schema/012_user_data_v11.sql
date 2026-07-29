-- 用户数据库 v11：为版本化配装偏好增加副词条筛选行为。
CREATE TABLE optimization_preference_substat_blacklist (
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

CREATE TABLE optimization_preference_substat_behavior (
    profile_version_id INTEGER NOT NULL,
    character_id INTEGER NOT NULL,
    equal_priority INTEGER NOT NULL DEFAULT 0 CHECK (equal_priority IN (0, 1)),
    ignore_grade_limit INTEGER NOT NULL DEFAULT 0 CHECK (ignore_grade_limit IN (0, 1)),
    min_grade_limit TEXT NOT NULL DEFAULT 'A'
        CHECK (min_grade_limit IN ('D', 'C', 'B', 'A', 'S', 'SS', 'SSS', 'ACE')),
    crit_threshold REAL CHECK (
        crit_threshold IS NULL OR (crit_threshold >= 0 AND crit_threshold <= 100)
    ),
    PRIMARY KEY (profile_version_id, character_id),
    FOREIGN KEY (profile_version_id, character_id)
        REFERENCES optimization_preference_character(profile_version_id, character_id)
        ON DELETE CASCADE
);
