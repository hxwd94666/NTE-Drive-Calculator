-- 用户数据库 v27：每场战报保存一个用户确认的单目标战斗条件。

CREATE TABLE battle_target_condition (
    battle_record_id INTEGER PRIMARY KEY
        REFERENCES battle_record(battle_record_id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL DEFAULT 'user_confirmed'
        CHECK (source_kind = 'user_confirmed'),
    target_name TEXT NOT NULL CHECK (length(trim(target_name)) > 0),
    enemy_level REAL NOT NULL CHECK (enemy_level BETWEEN 1 AND 999),
    scene TEXT NOT NULL CHECK (scene IN ('outer_realm', 'open_world')),
    defense_reduction REAL NOT NULL CHECK (defense_reduction BETWEEN -1 AND 1),
    vulnerability REAL NOT NULL CHECK (vulnerability BETWEEN -1 AND 10),
    resistance_chaos REAL NOT NULL CHECK (resistance_chaos BETWEEN -5 AND 5),
    resistance_cosmos REAL NOT NULL CHECK (resistance_cosmos BETWEEN -5 AND 5),
    resistance_incantation REAL NOT NULL CHECK (resistance_incantation BETWEEN -5 AND 5),
    resistance_lakshana REAL NOT NULL CHECK (resistance_lakshana BETWEEN -5 AND 5),
    resistance_nature REAL NOT NULL CHECK (resistance_nature BETWEEN -5 AND 5),
    resistance_psyche REAL NOT NULL CHECK (resistance_psyche BETWEEN -5 AND 5),
    resistance_psychically REAL NOT NULL
        CHECK (resistance_psychically BETWEEN -5 AND 5),
    updated_at_utc TEXT NOT NULL
);
