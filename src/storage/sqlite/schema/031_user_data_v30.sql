-- 用户数据库 v30：战报角色属性快照保留人物、弧盘、好感度和装备来源。

CREATE TABLE battle_character_stat_snapshot_v30 (
    battle_record_id INTEGER NOT NULL,
    character_id INTEGER NOT NULL,
    source_group TEXT NOT NULL
        CHECK (source_group IN (
            'character', 'fork', 'likeability', 'equipment', 'resolved'
        )),
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

INSERT INTO battle_character_stat_snapshot_v30(
    battle_record_id, character_id, source_group, property_id,
    display_name, value, is_percent, ordinal
)
SELECT battle_record_id, character_id, source_group, property_id,
       display_name, value, is_percent, ordinal
FROM battle_character_stat_snapshot;

DROP TABLE battle_character_stat_snapshot;
ALTER TABLE battle_character_stat_snapshot_v30
    RENAME TO battle_character_stat_snapshot;

CREATE INDEX idx_battle_character_stat_snapshot_character
    ON battle_character_stat_snapshot(
        battle_record_id, character_id, source_group, ordinal
    );
