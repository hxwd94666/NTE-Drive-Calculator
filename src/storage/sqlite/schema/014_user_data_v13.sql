-- 用户数据库 v13：保存账号战报摘要、保留状态和页面恢复指针。
CREATE TABLE battle_record (
    battle_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_operation_id TEXT NOT NULL UNIQUE,
    source_kind TEXT NOT NULL
        CHECK (source_kind IN ('nte_core_summary')),
    capability_level TEXT NOT NULL
        CHECK (capability_level IN ('summary_only')),
    combat_context_kind TEXT NOT NULL
        CHECK (combat_context_kind IN ('abyss', 'non_abyss')),
    abyss_floor INTEGER CHECK (abyss_floor IS NULL OR abyss_floor >= 1),
    has_first_half INTEGER NOT NULL CHECK (has_first_half IN (0, 1)),
    has_second_half INTEGER NOT NULL CHECK (has_second_half IN (0, 1)),
    captured_at_utc TEXT NOT NULL,
    finalized_at_utc TEXT NOT NULL,
    dps_time_mode TEXT NOT NULL,
    duration_seconds REAL NOT NULL CHECK (duration_seconds >= 0),
    total_damage REAL NOT NULL CHECK (total_damage >= 0),
    total_dps REAL NOT NULL CHECK (total_dps >= 0),
    total_damage_taken REAL NOT NULL CHECK (total_damage_taken >= 0),
    total_hits INTEGER NOT NULL CHECK (total_hits >= 0),
    character_count INTEGER NOT NULL CHECK (character_count >= 0),
    skill_count INTEGER NOT NULL CHECK (skill_count >= 0),
    character_ids_json TEXT NOT NULL,
    abyss_detected INTEGER NOT NULL CHECK (abyss_detected IN (0, 1)),
    abyss_success INTEGER NOT NULL CHECK (abyss_success IN (0, 1)),
    payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version >= 1),
    raw_summary_json TEXT NOT NULL,
    raw_summary_sha256 TEXT NOT NULL CHECK (length(raw_summary_sha256) = 64),
    created_at_utc TEXT NOT NULL,
    CHECK (
        (combat_context_kind = 'abyss' AND abyss_detected = 1)
        OR (combat_context_kind = 'non_abyss' AND abyss_detected = 0)
    ),
    CHECK (combat_context_kind = 'abyss' OR abyss_floor IS NULL)
);

CREATE INDEX idx_battle_record_finalized
    ON battle_record(finalized_at_utc DESC, battle_record_id DESC);
CREATE INDEX idx_battle_record_context
    ON battle_record(combat_context_kind, abyss_floor, finalized_at_utc DESC);

CREATE TABLE battle_record_retention (
    battle_record_id INTEGER PRIMARY KEY
        REFERENCES battle_record(battle_record_id) ON DELETE CASCADE,
    retention_kind TEXT NOT NULL CHECK (retention_kind IN ('auto', 'manual')),
    auto_saved_at_utc TEXT NOT NULL,
    manual_saved_at_utc TEXT,
    updated_at_utc TEXT NOT NULL,
    CHECK (
        (retention_kind = 'auto' AND manual_saved_at_utc IS NULL)
        OR (retention_kind = 'manual' AND manual_saved_at_utc IS NOT NULL)
    )
);

CREATE INDEX idx_battle_record_retention_auto
    ON battle_record_retention(auto_saved_at_utc, battle_record_id)
    WHERE retention_kind = 'auto';
CREATE INDEX idx_battle_record_retention_manual
    ON battle_record_retention(manual_saved_at_utc, battle_record_id)
    WHERE retention_kind = 'manual';

CREATE TABLE battle_report_page_state (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    last_battle_record_id INTEGER
        REFERENCES battle_record(battle_record_id) ON DELETE SET NULL,
    last_detail_scope TEXT NOT NULL DEFAULT 'current'
        CHECK (last_detail_scope IN ('current', 'first', 'second')),
    updated_at_utc TEXT NOT NULL
);
