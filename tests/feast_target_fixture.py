# 构建独立静态赏宴夹具，覆盖实际 schema、正式画像与每个目标的不同等级和来源。
from pathlib import Path
import sqlite3

from tools.game_data.static_database_build_support import SCHEMA_PATHS


def feast_database(root: Path, *, version=44):
    path = root / "feast.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        for script in SCHEMA_PATHS:
            number = int(script.name.split("_", 1)[0])
            if number <= version:
                db.executescript(script.read_text(encoding="utf-8"))
                db.execute("INSERT INTO schema_migration VALUES(?,?)", (number,"2026-10-27"))
        db.execute("INSERT INTO dataset VALUES('cn_test_fixture',53,'2026-10-27')")
        db.execute("INSERT INTO dataset_scope VALUES('cn_test_fixture','reference')")
        db.execute("INSERT INTO source_file VALUES(1,'fixture.json',?,3)", ('a' * 64,))
        for number in range(1,4):
            db.execute("INSERT INTO source_row VALUES(?,?,?,NULL,?)", (number,1,str(number),'b' * 64))
        db.execute("INSERT INTO feast_stage(stage_id,name_zh,boss_monster_id,special_high_difficulty,source_row_id) "
                   "VALUES('stage','合成关卡',?,0,1)", (None if version>=44 else 'boss_a',))
        for identity, level, hp, defense in [('boss_a',60,1000,100),('mon_b',47,3000,200)]:
            pack = identity + '_pack'
            db.execute("INSERT INTO monster_instance_profile VALUES('monster_static_big_world',?,?,'standard',?,NULL,1)", (identity,level,pack))
            db.execute("INSERT INTO enemy_combat_profile VALUES('standard',?,?,0,0,0,50,1,1,0,0,0,1,?,0,0)", (pack,defense,hp))
            db.execute("INSERT INTO enemy_element_resistance VALUES('standard',?,'normal',.2,0)", (pack,))
            if version>=44:
                db.execute("INSERT INTO feast_stage_difficulty VALUES('stage',1,'monster_static_big_world',?,'简单',?,500,1,?,'standard',?,NULL,3,2)",
                           (identity,identity,level,pack))
            elif identity=='boss_a':
                db.execute("INSERT INTO feast_stage_difficulty VALUES('stage',1,'简单','首领',500,1,?,'standard',?,NULL)", (level,pack))
    return path
