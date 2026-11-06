-- 合并静态 v44：保留 v40–42 的机制、预览和参考角色，并为全部正式目标保存独立画像。
-- 新父表与全部子表一起迁移，禁止 SQLite 重命名把外键留在临时旧表上。
DROP VIEW feast_all_stage_option;
CREATE TABLE feast_stage_v44 (
    stage_id TEXT PRIMARY KEY,
    name_zh TEXT NOT NULL CHECK (length(trim(name_zh)) > 0),
    boss_monster_id TEXT,
    special_high_difficulty INTEGER NOT NULL CHECK (special_high_difficulty IN (0,1)),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    mechanism_category_title TEXT,
    mechanism_score_limit INTEGER CHECK (mechanism_score_limit >= 0)
);
INSERT INTO feast_stage_v44 SELECT *,NULL,NULL FROM feast_stage;
CREATE TABLE feast_stage_difficulty_v44 (
    stage_id TEXT NOT NULL REFERENCES feast_stage_v44(stage_id),
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
INSERT INTO feast_stage_difficulty_v44
SELECT d.stage_id,d.difficulty_id,'monster_static_big_world',s.boss_monster_id,
       d.name_zh,d.boss_name_zh,d.base_score,d.score_rate,d.monster_level,d.profile_set,
       d.pack_id,d.boss_icon_path,s.source_row_id,s.source_row_id
FROM feast_stage_difficulty d JOIN feast_stage s USING(stage_id);
CREATE TABLE feast_stage_option_v44 (
    stage_id TEXT NOT NULL REFERENCES feast_stage_v44(stage_id),
    category_ordinal INTEGER NOT NULL CHECK(category_ordinal >= 0),
    option_ordinal INTEGER NOT NULL CHECK(option_ordinal >= 0),
    category_name_zh TEXT NOT NULL,
    option_id TEXT NOT NULL REFERENCES feast_option(option_id),
    PRIMARY KEY(stage_id,category_ordinal,option_ordinal)
);
INSERT INTO feast_stage_option_v44 SELECT * FROM feast_stage_option;
CREATE TABLE feast_stage_member_v44 (
    stage_id TEXT NOT NULL REFERENCES feast_stage_v44(stage_id),
    member_ordinal INTEGER NOT NULL,
    monster_id TEXT,
    preview_asset_path TEXT NOT NULL,
    PRIMARY KEY(stage_id,member_ordinal)
);
INSERT INTO feast_stage_member_v44 SELECT * FROM feast_stage_member;
CREATE TABLE feast_stage_mechanism_option_v44 (
    stage_id TEXT NOT NULL REFERENCES feast_stage_v44(stage_id),
    option_ordinal INTEGER NOT NULL,
    option_id TEXT NOT NULL REFERENCES feast_option(option_id),
    category_name_zh TEXT,
    score_limit INTEGER,
    PRIMARY KEY(stage_id,option_ordinal)
);
INSERT INTO feast_stage_mechanism_option_v44 SELECT * FROM feast_stage_mechanism_option;
DROP TABLE feast_stage_difficulty;
DROP TABLE feast_stage_option;
DROP TABLE feast_stage_member;
DROP TABLE feast_stage_mechanism_option;
DROP TABLE feast_stage;
ALTER TABLE feast_stage_v44 RENAME TO feast_stage;
ALTER TABLE feast_stage_difficulty_v44 RENAME TO feast_stage_difficulty;
ALTER TABLE feast_stage_option_v44 RENAME TO feast_stage_option;
ALTER TABLE feast_stage_member_v44 RENAME TO feast_stage_member;
ALTER TABLE feast_stage_mechanism_option_v44 RENAME TO feast_stage_mechanism_option;
CREATE INDEX idx_feast_difficulty_target
    ON feast_stage_difficulty(boss_monster_id,stage_id,difficulty_id);
CREATE VIEW feast_all_stage_option AS
 SELECT stage_id,category_ordinal,option_ordinal,category_name_zh,option_id FROM feast_stage_option
 UNION ALL
 SELECT m.stage_id,
  COALESCE((SELECT MAX(s.category_ordinal)+1 FROM feast_stage_option s WHERE s.stage_id=m.stage_id),0)+m.option_ordinal,
  0,m.category_name_zh || ' ' || CAST(m.option_ordinal+1 AS TEXT),m.option_id
 FROM feast_stage_mechanism_option m;
ALTER TABLE feast_option ADD COLUMN description_zh TEXT;
CREATE TABLE feast_option_effect (
    option_id TEXT NOT NULL REFERENCES feast_option(option_id),
    recipient_kind TEXT NOT NULL CHECK (recipient_kind IN ('boss','player')),
    effect_ordinal INTEGER NOT NULL CHECK (effect_ordinal >= 0),
    buff_asset_path TEXT NOT NULL CHECK (length(trim(buff_asset_path)) > 0),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY(option_id,recipient_kind,effect_ordinal)
);
