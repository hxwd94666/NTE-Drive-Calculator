# 验证赏宴以正式副本刷怪关系保留全部目标，升级不丢旧难度、条件及来源。
from pathlib import Path
import sqlite3

import pytest

from tools.game_data.build_static_database import StaticDatabaseBuilder
from tools.game_data.static_database_build_support import StaticDatabaseError


NTE_TEST_TIER = "core"
ROOT = Path(__file__).resolve().parents[2]


def schema(connection, version):
    connection.executescript((ROOT / "src/storage/sqlite/schema" / version).read_text(encoding="utf-8"))


@pytest.fixture
def database():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE source_row(source_row_id INTEGER PRIMARY KEY);
        INSERT INTO source_row VALUES(1),(2),(3);
        CREATE TABLE monster_instance_profile(static_table TEXT,monster_id TEXT,
            PRIMARY KEY(static_table,monster_id));
        CREATE TABLE monster_instance_profile_variant(static_table,monster_id,variant_kind,threshold_level,profile_set,pack_id);
        CREATE TABLE clone_activity(clone_id TEXT PRIMARY KEY,source_row_id INTEGER);
        CREATE TABLE clone_activity_difficulty(clone_id,difficulty_ordinal,difficulty_level,spawn_id);
        CREATE TABLE clone_spawn_member(spawn_id,wave_ordinal,entry_ordinal,monster_template_name,source_row_id,configured_monster_level);
        CREATE TABLE outer_realm_rotation(level_config_id TEXT PRIMARY KEY,inference_ordinal INTEGER);
        CREATE TABLE monster_template_binding(monster_template_name,monster_manual_id);
        CREATE TABLE monster_catalog(monster_manual_id,name_zh);
        INSERT INTO monster_instance_profile VALUES('monster_static_big_world','boss_actual'),('monster_static_big_world','mon_actual');
        INSERT INTO monster_instance_profile_variant VALUES
            ('monster_static_big_world','boss_actual','clone_level',60,'standard','boss_pack'),
            ('monster_static_big_world','mon_actual','clone_level',47,'standard','mon_pack');
        INSERT INTO clone_activity VALUES('stage',2);
        INSERT INTO clone_activity_difficulty VALUES('stage',0,1,'formal_spawn');
        INSERT INTO clone_spawn_member VALUES('formal_spawn',0,0,'boss_actual',3,60),('formal_spawn',0,1,'mon_actual',3,47);
    """)
    schema(db, "022_game_static_encounter_catalog.sql")
    yield db
    db.close()


def upgrade_feast_schema(db):
    for name in ("026_game_static_outer_realm_buff.sql", "037_game_static_season_evidence.sql",
                 "040_game_static_summon_season.sql", "041_game_static_feast_mechanisms.sql",
                 "044_game_static_feast_targets.sql"):
        schema(db, name)


def builder(db):
    upgrade_feast_schema(db)
    value = object.__new__(StaticDatabaseBuilder)
    value.connection = db
    value.source_row_ids = {("feast_stages", "stage"): 1}
    # 预览刻意与正式刷怪身份不同，防止回退路径解析或取数组第一项。
    value.rows = {"feast_mechanism_text": {}, "feast_options": {}, "feast_stages": {"stage": {
        "DescName": {"LocalizedString": "合成关卡"},
        "PreviewActorArray": [{"PreviewActorClass": {"AssetPathName": "/Game/Monster/wrong/UI"}}],
        "DifficultyMap": [{"Key": "1", "Value": {"Desc": {"LocalizedString": "简单"}, "BaseScore": 500, "ScoreRate": 1}}],
        "DifficultyDisplayData": [{"Key": "1", "Value": {"Desc": {"LocalizedString": "总览名称"}}}],
    }}}
    return value


def test_multi_target_import_uses_formal_spawns_and_each_own_level_and_pack(database):
    value = builder(database)
    value._import_feast_catalog()
    rows = [dict(row) for row in database.execute("SELECT * FROM feast_stage_difficulty ORDER BY boss_monster_id")]
    assert [(r["boss_monster_id"], r["monster_level"], r["pack_id"]) for r in rows] == [
        ("boss_actual", 60, "boss_pack"), ("mon_actual", 47, "mon_pack")]
    assert all(r["target_source_row_id"] == 3 and r["clone_source_row_id"] == 2 for r in rows)
    assert all(r["boss_name_zh"] is None for r in rows)  # 总览名不冒充每个目标的正式名称。
    assert database.execute("SELECT boss_monster_id FROM feast_stage").fetchone()[0] is None
    assert database.execute("PRAGMA foreign_key_check").fetchall() == []


def test_missing_formal_spawn_does_not_fall_back_to_preview(database):
    value = builder(database)
    database.execute("DELETE FROM clone_spawn_member")
    with pytest.raises(StaticDatabaseError, match="刷怪|目标"):
        value._import_feast_catalog()


def test_append_migration_preserves_single_target_difficulty_options_and_foreign_keys(database):
    database.execute("INSERT INTO feast_stage VALUES('legacy','旧关卡','boss_actual',0,1)")
    database.execute("INSERT INTO feast_stage_difficulty VALUES('legacy',1,'简单','旧首领',500,1,60,'standard','boss_pack',NULL)")
    database.execute("INSERT INTO feast_option VALUES('option','type','health_up',NULL,.1,NULL,5,NULL,1)")
    database.execute("INSERT INTO feast_stage_option VALUES('legacy',0,0,'生命','option')")
    upgrade_feast_schema(database)
    row = dict(database.execute("SELECT * FROM feast_stage_difficulty").fetchone())
    assert (row["stage_id"],row["boss_monster_id"],row["monster_level"],row["pack_id"]) == ('legacy','boss_actual',60,'boss_pack')
    assert database.execute("SELECT option_id FROM feast_stage_option").fetchone()[0] == 'option'
    assert database.execute("PRAGMA foreign_key_check").fetchall() == []


def test_single_target_display_name_yields_to_unique_family_formal_name(database):
    # 关卡换 Boss 后展示文案仍写旧名：同一数字家族在深渊刷怪中有唯一正式名时以其为准。
    database.executescript("""
        CREATE TABLE abyss_monster_pool_entry(monster_class_path TEXT, monster_name_zh TEXT);
        INSERT INTO abyss_monster_pool_entry VALUES
            ('/Game/Monster/boss_17/Abyss/Boss_017_BP_Abyss.Boss_017_BP_Abyss_C','玛门');
        INSERT INTO monster_instance_profile VALUES('monster_static_big_world','boss_17_BP_DiyBoss');
        INSERT INTO monster_instance_profile_variant VALUES
            ('monster_static_big_world','boss_17_BP_DiyBoss','clone_level',60,'standard','boss17_pack');
        DELETE FROM clone_spawn_member;
        INSERT INTO clone_spawn_member VALUES('formal_spawn',0,0,'boss_17_BP_DiyBoss',3,60);
    """)
    value = builder(database)
    value._import_feast_catalog()
    row = dict(database.execute("SELECT boss_monster_id, boss_name_zh FROM feast_stage_difficulty").fetchone())
    assert row == {"boss_monster_id": "boss_17_BP_DiyBoss", "boss_name_zh": "玛门"}
    # 家族没有其他正式名时，仍沿用关卡展示文案。
    database.execute("DELETE FROM abyss_monster_pool_entry")
    for table in ("feast_stage_option", "feast_stage_difficulty", "feast_stage_member", "feast_stage"):
        database.execute(f"DELETE FROM {table}")
    value._import_feast_catalog()
    assert database.execute("SELECT boss_name_zh FROM feast_stage_difficulty").fetchone()[0] == "总览名称"
