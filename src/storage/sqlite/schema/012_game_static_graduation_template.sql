-- 静态库 v12：构建期生成的角色直伤毕业模板；运行时只读。

CREATE TABLE character_graduation_template (
    character_id INTEGER PRIMARY KEY REFERENCES character(character_id),
    source_kind TEXT NOT NULL
        CHECK (source_kind IN ('legacy_role_config', 'official_default')),
    fork_id TEXT REFERENCES fork_item(fork_id),
    fork_level INTEGER,
    fork_refinement_level INTEGER,
    core_suit_id TEXT REFERENCES equipment_suit(suit_id),
    core_main_property_id TEXT REFERENCES equipment_attribute(attribute_id),
    drive_area INTEGER NOT NULL CHECK (drive_area = 20),
    extra_shape_count INTEGER NOT NULL CHECK (extra_shape_count >= 0),
    benchmark_damage REAL NOT NULL CHECK (benchmark_damage > 0),
    profile_json TEXT NOT NULL,
    equipment_json TEXT NOT NULL,
    generated_at_utc TEXT NOT NULL
);

CREATE INDEX idx_character_graduation_template_fork
    ON character_graduation_template(fork_id, character_id);
