-- 静态库 v23：游戏正式战斗类目、材料副本、波次与怪物模板绑定。

CREATE TABLE clone_activity_category (
    category_id TEXT PRIMARY KEY,
    clone_type TEXT NOT NULL UNIQUE,
    name_zh TEXT NOT NULL CHECK (length(trim(name_zh)) > 0),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE clone_activity (
    clone_id TEXT PRIMARY KEY,
    clone_type TEXT NOT NULL,
    category_id TEXT REFERENCES clone_activity_category(category_id),
    name_zh TEXT NOT NULL CHECK (length(trim(name_zh)) > 0),
    description_zh TEXT,
    show_in_adventure INTEGER NOT NULL CHECK (show_in_adventure IN (0, 1)),
    cross_scene INTEGER NOT NULL CHECK (cross_scene IN (0, 1)),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE INDEX idx_clone_activity_category
    ON clone_activity(category_id, show_in_adventure, clone_id);

CREATE TABLE clone_activity_difficulty (
    clone_id TEXT NOT NULL REFERENCES clone_activity(clone_id),
    difficulty_ordinal INTEGER NOT NULL CHECK (difficulty_ordinal >= 0),
    difficulty_level INTEGER NOT NULL CHECK (difficulty_level >= 0),
    team_level INTEGER NOT NULL CHECK (team_level >= 0),
    stamina_cost INTEGER NOT NULL CHECK (stamina_cost >= 0),
    drop_id TEXT,
    spawn_id TEXT,
    kill_monster_time_limit REAL CHECK (
        kill_monster_time_limit IS NULL OR kill_monster_time_limit >= 0
    ),
    PRIMARY KEY (clone_id, difficulty_ordinal)
);

CREATE INDEX idx_clone_activity_difficulty_spawn
    ON clone_activity_difficulty(spawn_id);

CREATE TABLE clone_spawn_member (
    spawn_id TEXT NOT NULL,
    wave_ordinal INTEGER NOT NULL CHECK (wave_ordinal >= 0),
    entry_ordinal INTEGER NOT NULL CHECK (entry_ordinal >= 0),
    monster_template_path TEXT NOT NULL,
    monster_template_name TEXT NOT NULL,
    monster_count INTEGER NOT NULL CHECK (monster_count >= 1),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (spawn_id, wave_ordinal, entry_ordinal)
);

CREATE INDEX idx_clone_spawn_member_template
    ON clone_spawn_member(monster_template_name);

CREATE TABLE monster_template_binding (
    monster_template_name TEXT NOT NULL,
    monster_manual_id TEXT NOT NULL REFERENCES monster_catalog(monster_manual_id),
    binding_kind TEXT NOT NULL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (monster_template_name, monster_manual_id)
);

CREATE INDEX idx_monster_template_binding_manual
    ON monster_template_binding(monster_manual_id, binding_kind);
