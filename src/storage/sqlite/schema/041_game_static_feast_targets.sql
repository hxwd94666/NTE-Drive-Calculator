-- 静态库 v41：赏宴每个难度保存全部正式刷怪目标及各自属性包，不以预览模型推断唯一 Boss。
ALTER TABLE feast_stage RENAME TO feast_stage_v22;
ALTER TABLE feast_stage_difficulty RENAME TO feast_stage_difficulty_v22;
ALTER TABLE feast_stage_option RENAME TO feast_stage_option_v22;

CREATE TABLE feast_stage (
    stage_id TEXT PRIMARY KEY,
    name_zh TEXT NOT NULL CHECK (length(trim(name_zh)) > 0),
    boss_monster_id TEXT,
    special_high_difficulty INTEGER NOT NULL CHECK (special_high_difficulty IN (0,1)),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);
INSERT INTO feast_stage SELECT * FROM feast_stage_v22;

CREATE TABLE feast_stage_difficulty (
    stage_id TEXT NOT NULL REFERENCES feast_stage(stage_id),
    difficulty_id INTEGER NOT NULL CHECK (difficulty_id BETWEEN 1 AND 4),
    boss_static_table TEXT NOT NULL,
    boss_monster_id TEXT NOT NULL,
    name_zh TEXT NOT NULL,
    boss_name_zh TEXT,
    base_score INTEGER NOT NULL,
    score_rate REAL NOT NULL,
    monster_level INTEGER NOT NULL,
    profile_set TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    boss_icon_path TEXT,
    target_source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    clone_source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY(stage_id,difficulty_id,boss_static_table,boss_monster_id),
    FOREIGN KEY(boss_static_table,boss_monster_id)
        REFERENCES monster_instance_profile(static_table,monster_id)
);
INSERT INTO feast_stage_difficulty
SELECT d.stage_id,d.difficulty_id,'monster_static_big_world',s.boss_monster_id,
       d.name_zh,d.boss_name_zh,d.base_score,d.score_rate,d.monster_level,d.profile_set,
       d.pack_id,d.boss_icon_path,s.source_row_id,s.source_row_id
FROM feast_stage_difficulty_v22 d JOIN feast_stage_v22 s USING(stage_id);

CREATE TABLE feast_stage_option (
    stage_id TEXT NOT NULL REFERENCES feast_stage(stage_id),
    category_ordinal INTEGER NOT NULL CHECK(category_ordinal >= 0),
    option_ordinal INTEGER NOT NULL CHECK(option_ordinal >= 0),
    category_name_zh TEXT NOT NULL,
    option_id TEXT NOT NULL REFERENCES feast_option(option_id),
    PRIMARY KEY(stage_id,category_ordinal,option_ordinal)
);
INSERT INTO feast_stage_option SELECT * FROM feast_stage_option_v22;
DROP TABLE feast_stage_difficulty_v22;
DROP TABLE feast_stage_option_v22;
DROP TABLE feast_stage_v22;
CREATE INDEX idx_feast_difficulty_target ON feast_stage_difficulty(boss_monster_id,stage_id,difficulty_id);
