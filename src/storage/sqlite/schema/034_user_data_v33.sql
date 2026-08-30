-- 用户数据库 v33：标记导入战报来源，并锁定包内逐角色空幕/驱动基线。

CREATE TABLE battle_report_import_origin (
    battle_record_id INTEGER PRIMARY KEY
        REFERENCES battle_record(battle_record_id) ON DELETE CASCADE,
    source_bundle_id TEXT NOT NULL CHECK (length(trim(source_bundle_id)) > 0),
    source_account_nickname TEXT NOT NULL
        CHECK (length(trim(source_account_nickname)) > 0),
    last_export_account_nickname TEXT NOT NULL
        CHECK (length(trim(last_export_account_nickname)) > 0),
    contract_version INTEGER NOT NULL CHECK (contract_version = 2),
    imported_at_utc TEXT NOT NULL
);

ALTER TABLE battle_axis_capture
    ADD COLUMN finalization_incomplete_reason TEXT;

ALTER TABLE battle_hit_evidence
    ADD COLUMN max_hp_reduction REAL
        CHECK (max_hp_reduction IS NULL OR max_hp_reduction >= 0);

CREATE TABLE battle_character_import_equipment_lock (
    battle_record_id INTEGER NOT NULL,
    character_id INTEGER NOT NULL CHECK (character_id > 0),
    equipment_source_kind TEXT NOT NULL
        CHECK (equipment_source_kind IN (
            'calibrated_build_edit',
            'frozen_battle_snapshot',
            'imported_locked'
        )),
    equipment_sha256 TEXT NOT NULL CHECK (length(equipment_sha256) = 64),
    locked_equipment_json TEXT NOT NULL CHECK (json_valid(locked_equipment_json)),
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY (battle_record_id, character_id),
    FOREIGN KEY (battle_record_id, character_id)
        REFERENCES battle_character_build_snapshot(battle_record_id, character_id)
        ON DELETE CASCADE
);
