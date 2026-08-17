-- 用户数据库 v22：副词条黑名单可改为驱动评分零权重而非硬过滤。
-- 个别 v21 本机库曾缺失 v11 行为表；先补齐旧表结构，再统一追加新列。
CREATE TABLE IF NOT EXISTS optimization_preference_substat_behavior (
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

ALTER TABLE optimization_preference_substat_behavior
    ADD COLUMN blacklist_zero_weight INTEGER NOT NULL DEFAULT 0
    CHECK (blacklist_zero_weight IN (0, 1));
