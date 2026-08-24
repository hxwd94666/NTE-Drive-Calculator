-- 用户数据库 v31：保存可复现的场景目标集合、争锋选项与魔女赐福。

ALTER TABLE battle_target_condition
    ADD COLUMN environment_kind TEXT NOT NULL DEFAULT 'manual'
        CHECK (environment_kind IN ('manual', 'open_world', 'outer_realm', 'feast'));

ALTER TABLE battle_target_condition
    ADD COLUMN environment_ref TEXT;

ALTER TABLE battle_target_condition
    ADD COLUMN selected_target_ids_json TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(selected_target_ids_json));

ALTER TABLE battle_target_condition
    ADD COLUMN primary_target_id TEXT;

ALTER TABLE battle_target_condition
    ADD COLUMN difficulty_id INTEGER
        CHECK (difficulty_id IS NULL OR difficulty_id BETWEEN 1 AND 4);

ALTER TABLE battle_target_condition
    ADD COLUMN feast_options_json TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(feast_options_json));

ALTER TABLE battle_target_condition
    ADD COLUMN witch_buff_id TEXT;

ALTER TABLE battle_target_condition
    ADD COLUMN witch_buff_name_zh TEXT;

ALTER TABLE battle_target_condition
    ADD COLUMN witch_buff_property_id TEXT;

ALTER TABLE battle_target_condition
    ADD COLUMN witch_buff_value REAL;

ALTER TABLE battle_target_condition
    ADD COLUMN witch_buff_is_percent INTEGER NOT NULL DEFAULT 0
        CHECK (witch_buff_is_percent IN (0, 1));

UPDATE battle_target_condition
SET environment_kind = CASE scene
    WHEN 'open_world' THEN 'open_world'
    ELSE 'outer_realm'
END;
