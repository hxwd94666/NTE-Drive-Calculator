-- 用户数据库 v25：战报长页统一时间读取、完整逐击标签与角色属性分析快照。

ALTER TABLE battle_hit_evidence ADD COLUMN target_context_json TEXT
    CHECK (target_context_json IS NULL OR json_valid(target_context_json));
ALTER TABLE battle_hit_evidence ADD COLUMN follow_up_damage_name TEXT;
ALTER TABLE battle_hit_evidence ADD COLUMN follow_up_damage_component TEXT;
ALTER TABLE battle_hit_evidence ADD COLUMN follow_up_attack_type TEXT;
ALTER TABLE battle_hit_evidence ADD COLUMN follow_up_damage_attribute TEXT;

CREATE INDEX idx_battle_hit_evidence_time
    ON battle_hit_evidence(capture_id, relative_time_us, sequence_order);

ALTER TABLE battle_build_snapshot ADD COLUMN formula_model_version TEXT;
ALTER TABLE battle_build_snapshot ADD COLUMN name_mapping_version TEXT;

CREATE TABLE battle_character_stat_snapshot (
    battle_record_id INTEGER NOT NULL,
    character_id INTEGER NOT NULL,
    source_group TEXT NOT NULL
        CHECK (source_group IN ('equipment', 'resolved')),
    property_id TEXT NOT NULL CHECK (length(trim(property_id)) > 0),
    display_name TEXT NOT NULL CHECK (length(trim(display_name)) > 0),
    value REAL NOT NULL,
    is_percent INTEGER NOT NULL CHECK (is_percent IN (0, 1)),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (battle_record_id, character_id, source_group, property_id),
    FOREIGN KEY (battle_record_id, character_id)
        REFERENCES battle_character_build_snapshot(battle_record_id, character_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_battle_character_stat_snapshot_character
    ON battle_character_stat_snapshot(battle_record_id, character_id, source_group, ordinal);

ALTER TABLE battle_report_page_state ADD COLUMN analysis_start_us INTEGER
    CHECK (analysis_start_us IS NULL OR analysis_start_us >= 0);
ALTER TABLE battle_report_page_state ADD COLUMN analysis_end_us INTEGER
    CHECK (analysis_end_us IS NULL OR analysis_end_us >= 0);
ALTER TABLE battle_report_page_state ADD COLUMN analysis_character_id INTEGER
    CHECK (analysis_character_id IS NULL OR analysis_character_id > 0);
