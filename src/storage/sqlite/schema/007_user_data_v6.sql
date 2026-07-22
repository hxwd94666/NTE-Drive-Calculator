-- 用户数据库 v6：角色页只保存官方 ID 指针和账号养成状态。

CREATE TABLE character_profile (
    character_id INTEGER PRIMARY KEY CHECK (character_id > 0),
    character_level INTEGER NOT NULL CHECK (character_level BETWEEN 1 AND 80),
    breakthrough_stage INTEGER NOT NULL CHECK (breakthrough_stage BETWEEN 0 AND 6),
    awakening_level INTEGER NOT NULL CHECK (awakening_level BETWEEN 0 AND 6),
    fork_id TEXT,
    fork_level INTEGER,
    fork_refinement_level INTEGER,
    selected_skill_id TEXT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    CHECK (
        (fork_id IS NULL AND fork_level IS NULL AND fork_refinement_level IS NULL)
        OR
        (fork_id IS NOT NULL AND length(trim(fork_id)) > 0
         AND fork_level BETWEEN 1 AND 80
         AND fork_refinement_level BETWEEN 1 AND 5)
    )
);

CREATE INDEX idx_character_profile_active_order
    ON character_profile(ordinal) WHERE is_active = 1;

CREATE TABLE character_profile_skill (
    character_id INTEGER NOT NULL
        REFERENCES character_profile(character_id) ON DELETE CASCADE,
    skill_id TEXT NOT NULL CHECK (length(trim(skill_id)) > 0),
    skill_level INTEGER NOT NULL CHECK (skill_level > 0),
    PRIMARY KEY (character_id, skill_id)
);
