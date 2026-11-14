# 为构建连接借用边界提供全迁移的最小合成SQLite，不读取发行库或解包数据。
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from tools.game_data.static_database_build_support import SCHEMA_PATHS

CHARACTER_ID = 2001
PROPERTIES = (("CritBase", "暴击率%"), ("CritDamageBase", "暴击伤害%"),
              ("DamageUpGeneralBase", "伤害增加%"), ("AtkUp", "攻击力%"))


def build_fixture(directory: Path, *, factory=sqlite3.Connection):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "building.tmp"
    config = directory / "config"
    config.mkdir()
    stats = {"gold_base_values": {name: 1.0 for _, name in PROPERTIES},
             "tape_stat_values": {name: 10.0 for _, name in PROPERTIES},
             "tape_main_stat_values": {"攻击力%": 37.5}}
    (config / "stats.json").write_text(json.dumps(stats), encoding="utf-8")
    connection = sqlite3.connect(path, factory=factory)
    for schema in SCHEMA_PATHS:
        connection.executescript(schema.read_text(encoding="utf-8"))
    stamp = "2026-01-01T00:00:00Z"
    connection.executemany("INSERT INTO schema_migration VALUES (?, ?)",
                           [(int(schema.name.split("_", 1)[0]), stamp) for schema in SCHEMA_PATHS])
    connection.execute("INSERT INTO dataset VALUES ('builder-fixture', 51, ?)", (stamp,))
    connection.execute("INSERT INTO dataset_scope VALUES ('builder-fixture', 'game')")
    connection.execute("INSERT INTO source_file VALUES (1, 'fixture.json', ?, 1)", ("0" * 64,))
    connection.execute("INSERT INTO source_row VALUES (1, 1, 'fixture', NULL, ?)", ("0" * 64,))
    connection.execute("INSERT INTO character(character_id,name_zh,group_type,source_row_id) VALUES (?, '合成角色', 'fixture', 1)", (CHARACTER_ID,))
    connection.execute("INSERT INTO character_annotation VALUES (?, 'fixture-role', ?, 'available_character', 'fixture')", (CHARACTER_ID, CHARACTER_ID))
    connection.execute("INSERT INTO character_panel_growth VALUES (?,80,6,'max_level',1000,100,50,1,1,1)", (CHARACTER_ID,))
    for property_id, name in PROPERTIES:
        connection.execute("INSERT INTO equipment_attribute(attribute_id,display_name_zh,filter_name_zh,show_percent,show_outside,show_inside,source_row_id) VALUES (?,?,?,1,1,1,1)", (property_id, name, name))
    connection.execute("INSERT INTO character_weight_recommendation(character_id,source_kind,source_updated_at_utc) VALUES (?, 'workshop_cache', ?)", (CHARACTER_ID, stamp))
    connection.executemany("INSERT INTO character_weight_recommendation_property VALUES (?, ?, 1, 1, ?)",
                           [(CHARACTER_ID, property_id, ordinal) for ordinal, (property_id, _) in enumerate(PROPERTIES)])
    connection.execute("INSERT INTO character_graduation_template(character_id,source_kind,core_main_property_id,drive_area,extra_shape_count,benchmark_damage,profile_json,equipment_json,generated_at_utc) VALUES (?, 'official_default','AtkUp',20,0,1,'{}','[]',?)", (CHARACTER_ID, stamp))
    connection.execute("CREATE TABLE build_owner_sentinel(value TEXT)")
    connection.execute("INSERT INTO build_owner_sentinel VALUES ('owner-original')")
    connection.commit()
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    return connection, path, config
