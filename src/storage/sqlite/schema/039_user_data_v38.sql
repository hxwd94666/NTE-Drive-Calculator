-- 用户数据库 v38：持久化版本化的战报自动目标推断派生快照。

CREATE TABLE battle_inferred_target_snapshot (
    battle_record_id INTEGER PRIMARY KEY
        REFERENCES battle_record(battle_record_id) ON DELETE CASCADE,
    payload_schema_version INTEGER NOT NULL
        CHECK (payload_schema_version >= 1),
    algorithm_version TEXT NOT NULL
        CHECK (length(trim(algorithm_version)) > 0),
    static_dataset_id TEXT,
    static_schema_version INTEGER
        CHECK (static_schema_version IS NULL OR static_schema_version >= 1),
    inference_status TEXT NOT NULL
        CHECK (inference_status IN ('resolved', 'unresolved')),
    environment_kind TEXT,
    environment_ref TEXT,
    environment_name TEXT,
    source_kind TEXT,
    confidence TEXT,
    inferred_payload_json TEXT NOT NULL
        CHECK (json_valid(inferred_payload_json)),
    updated_at_utc TEXT NOT NULL
);
