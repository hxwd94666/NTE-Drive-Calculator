-- 用户数据库 v23：持久化 nte-core 逐击轴，并在战后物化实际出场角色的游戏当前配装快照。

ALTER TABLE battle_record ADD COLUMN evidence_source_kind TEXT;
ALTER TABLE battle_record ADD COLUMN evidence_capability_level TEXT;
ALTER TABLE battle_record ADD COLUMN nte_core_record_id TEXT;
ALTER TABLE battle_record ADD COLUMN nte_core_contract_version INTEGER
    CHECK (nte_core_contract_version IS NULL OR nte_core_contract_version >= 1);
ALTER TABLE battle_record ADD COLUMN axis_complete INTEGER
    CHECK (axis_complete IS NULL OR axis_complete IN (0, 1));
ALTER TABLE battle_record ADD COLUMN axis_first_sequence TEXT;
ALTER TABLE battle_record ADD COLUMN axis_total_hits TEXT;
ALTER TABLE battle_record ADD COLUMN axis_stored_hits INTEGER NOT NULL DEFAULT 0
    CHECK (axis_stored_hits >= 0);

CREATE TABLE battle_axis_capture (
    capture_id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_operation_id TEXT NOT NULL UNIQUE,
    battle_record_id INTEGER UNIQUE
        REFERENCES battle_record(battle_record_id) ON DELETE CASCADE,
    capture_state TEXT NOT NULL
        CHECK (capture_state IN ('capturing', 'finalized')),
    source_inventory_snapshot_id INTEGER
        REFERENCES inventory_snapshot(snapshot_id) ON DELETE SET NULL,
    account_generation INTEGER NOT NULL CHECK (account_generation >= 0),
    static_dataset_id TEXT,
    static_schema_version INTEGER,
    source_battle_record_id TEXT,
    contract_version INTEGER
        CHECK (contract_version IS NULL OR contract_version >= 1),
    source_generation TEXT,
    axis_complete INTEGER NOT NULL DEFAULT 1 CHECK (axis_complete IN (0, 1)),
    first_available_cursor TEXT,
    next_cursor TEXT,
    first_sequence TEXT,
    total_hits TEXT,
    retained_hits INTEGER CHECK (retained_hits IS NULL OR retained_hits >= 0),
    stored_hits INTEGER NOT NULL DEFAULT 0 CHECK (stored_hits >= 0),
    captured_at_utc TEXT NOT NULL,
    finalized_at_utc TEXT,
    raw_record_json TEXT,
    raw_record_sha256 TEXT
        CHECK (raw_record_sha256 IS NULL OR length(raw_record_sha256) = 64),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    CHECK (
        (capture_state = 'capturing' AND battle_record_id IS NULL)
        OR (capture_state = 'finalized' AND battle_record_id IS NOT NULL)
    )
);

CREATE INDEX idx_battle_axis_capture_source_snapshot
    ON battle_axis_capture(source_inventory_snapshot_id)
    WHERE source_inventory_snapshot_id IS NOT NULL;

CREATE TABLE battle_hit_evidence (
    capture_id INTEGER NOT NULL
        REFERENCES battle_axis_capture(capture_id) ON DELETE CASCADE,
    sequence_text TEXT NOT NULL,
    sequence_order INTEGER NOT NULL CHECK (sequence_order >= 0),
    timestamp_unix_us INTEGER,
    relative_time_us INTEGER,
    abyss_half TEXT,
    character_id INTEGER,
    character_name TEXT,
    character_known INTEGER NOT NULL CHECK (character_known IN (0, 1)),
    character_source TEXT,
    attribution_status TEXT,
    attribution_source TEXT,
    attribution_unknown_reason TEXT,
    team_snapshot_id TEXT,
    direction TEXT NOT NULL,
    damage REAL NOT NULL CHECK (damage >= 0),
    follow_up_damage REAL NOT NULL CHECK (follow_up_damage >= 0),
    total_damage REAL NOT NULL CHECK (total_damage >= 0),
    follow_up_timestamp_unix_us INTEGER,
    target_id TEXT,
    target_name TEXT,
    target_name_en TEXT,
    target_name_ja TEXT,
    target_monster_id TEXT,
    target_context TEXT,
    target_hp_before REAL,
    target_hp_after REAL,
    target_max_hp REAL,
    target_hp_percent REAL,
    gameplay_effect_index INTEGER,
    gameplay_effect_name TEXT,
    ability_name TEXT,
    damage_name TEXT,
    damage_component TEXT,
    attack_type TEXT,
    damage_attribute TEXT,
    follow_up_labels_json TEXT NOT NULL CHECK (json_valid(follow_up_labels_json)),
    raw_hit_json TEXT NOT NULL CHECK (json_valid(raw_hit_json)),
    PRIMARY KEY (capture_id, sequence_text),
    UNIQUE (capture_id, sequence_order)
);

CREATE INDEX idx_battle_hit_evidence_character_time
    ON battle_hit_evidence(capture_id, character_id, relative_time_us);
CREATE INDEX idx_battle_hit_evidence_target_time
    ON battle_hit_evidence(capture_id, target_id, relative_time_us);

CREATE TABLE battle_time_stop_interval (
    capture_id INTEGER NOT NULL
        REFERENCES battle_axis_capture(capture_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    start_unix_us INTEGER,
    end_unix_us INTEGER,
    duration_us INTEGER CHECK (duration_us IS NULL OR duration_us >= 0),
    raw_interval_json TEXT NOT NULL CHECK (json_valid(raw_interval_json)),
    PRIMARY KEY (capture_id, ordinal)
);

CREATE TABLE battle_build_snapshot (
    battle_record_id INTEGER PRIMARY KEY
        REFERENCES battle_record(battle_record_id) ON DELETE CASCADE,
    source_inventory_snapshot_id INTEGER
        REFERENCES inventory_snapshot(snapshot_id) ON DELETE SET NULL,
    account_generation INTEGER NOT NULL CHECK (account_generation >= 0),
    static_dataset_id TEXT,
    static_schema_version INTEGER,
    profile_schema_version INTEGER NOT NULL CHECK (profile_schema_version >= 1),
    observed_character_count INTEGER NOT NULL CHECK (observed_character_count >= 0),
    materialized_at_utc TEXT NOT NULL
);

CREATE TABLE battle_character_build_snapshot (
    battle_record_id INTEGER NOT NULL
        REFERENCES battle_build_snapshot(battle_record_id) ON DELETE CASCADE,
    character_id INTEGER NOT NULL CHECK (character_id > 0),
    observed_name TEXT,
    profile_source TEXT NOT NULL,
    character_level INTEGER NOT NULL CHECK (character_level BETWEEN 1 AND 80),
    breakthrough_stage INTEGER NOT NULL CHECK (breakthrough_stage BETWEEN 0 AND 6),
    awakening_level INTEGER NOT NULL CHECK (awakening_level BETWEEN 0 AND 6),
    fork_id TEXT,
    fork_level INTEGER,
    fork_refinement_level INTEGER,
    selected_skill_id TEXT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    raw_profile_json TEXT NOT NULL CHECK (json_valid(raw_profile_json)),
    PRIMARY KEY (battle_record_id, character_id)
);

CREATE TABLE battle_character_skill_snapshot (
    battle_record_id INTEGER NOT NULL,
    character_id INTEGER NOT NULL,
    skill_id TEXT NOT NULL,
    skill_level INTEGER NOT NULL CHECK (skill_level > 0),
    PRIMARY KEY (battle_record_id, character_id, skill_id),
    FOREIGN KEY (battle_record_id, character_id)
        REFERENCES battle_character_build_snapshot(battle_record_id, character_id)
        ON DELETE CASCADE
);

CREATE TABLE battle_equipment_snapshot (
    battle_record_id INTEGER NOT NULL,
    character_id INTEGER NOT NULL,
    uid_serial INTEGER NOT NULL CHECK (uid_serial > 0),
    uid_slot INTEGER NOT NULL CHECK (uid_slot > 0),
    kind TEXT NOT NULL CHECK (kind IN ('core', 'module')),
    item_id TEXT NOT NULL,
    suit_id TEXT,
    geometry TEXT,
    grid_count INTEGER NOT NULL CHECK (grid_count >= 0),
    quality TEXT,
    level INTEGER,
    max_level INTEGER,
    locked INTEGER NOT NULL CHECK (locked IN (0, 1)),
    target_row INTEGER,
    target_column INTEGER,
    names_json TEXT NOT NULL CHECK (json_valid(names_json)),
    suit_names_json TEXT NOT NULL CHECK (json_valid(suit_names_json)),
    raw_item_json TEXT NOT NULL CHECK (json_valid(raw_item_json)),
    PRIMARY KEY (battle_record_id, uid_serial, uid_slot),
    FOREIGN KEY (battle_record_id, character_id)
        REFERENCES battle_character_build_snapshot(battle_record_id, character_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_battle_equipment_snapshot_character
    ON battle_equipment_snapshot(battle_record_id, character_id, kind);

CREATE TABLE battle_equipment_stat_snapshot (
    battle_record_id INTEGER NOT NULL,
    uid_serial INTEGER NOT NULL,
    uid_slot INTEGER NOT NULL,
    stat_group TEXT NOT NULL CHECK (stat_group IN ('main', 'sub')),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    property_id TEXT NOT NULL,
    value REAL NOT NULL,
    is_percent INTEGER NOT NULL CHECK (is_percent IN (0, 1)),
    names_json TEXT NOT NULL CHECK (json_valid(names_json)),
    raw_stat_json TEXT NOT NULL CHECK (json_valid(raw_stat_json)),
    PRIMARY KEY (
        battle_record_id, uid_serial, uid_slot, stat_group, ordinal
    ),
    FOREIGN KEY (battle_record_id, uid_serial, uid_slot)
        REFERENCES battle_equipment_snapshot(battle_record_id, uid_serial, uid_slot)
        ON DELETE CASCADE
);
