# 验证新版赛季按正式曲线和实际子效果路径入库，不从终结技事件猜全队受益对象。
from pathlib import Path
import sqlite3

from tools.game_data.build_static_database import StaticDatabaseBuilder


NTE_TEST_TIER = "core"
ROOT = Path(__file__).resolve().parents[2]
EFFECT = "/Game/Blueprints/Abilities/Buff/Abyss/Phases/P14/Buff_Abyss_Phase_014"


def test_abyss14_preserves_six_attributes_recipient_evidence_duration_and_refresh_limit():
    with sqlite3.connect(":memory:") as db:
        db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE source_row(source_row_id INTEGER PRIMARY KEY);
            INSERT INTO source_row VALUES(1),(2),(3),(4),(5);
            CREATE TABLE outer_realm_rotation(level_config_id TEXT PRIMARY KEY,inference_ordinal INTEGER);
            INSERT INTO outer_realm_rotation VALUES('Abyss_14',14);
        """)
        for name in ("026_game_static_outer_realm_buff.sql", "037_game_static_season_evidence.sql"):
            db.executescript((ROOT / "src/storage/sqlite/schema" / name).read_text(encoding="utf-8"))
        builder = object.__new__(StaticDatabaseBuilder)
        builder.connection = db
        curves = {"Buff_Abyss_Phase_014_DefIgnore": .2, "Buff_Abyss_Phase_014_Penetrate": .2,
                  "Buff_Abyss_Phase_014_CD": 20}
        builder.rows = {
            "abyss_buff_curves": {key: {"Keys": [{"Time": 1, "Value": value}]} for key, value in curves.items()},
            "abyss_seasons": {"Abyss_14": {"BuffID": "abyss_14_buff_01", "SeasonName": {"LocalizedString": "赛季"}}},
            "abyss_buff_configs": {"abyss_14_buff_01": {
                "BuffName": {"LocalizedString": "效果"}, "BuffDesc": {"LocalizedString": "正式描述"},
                "CloneBuffArray": [{"BuffGE": {"AssetPathName": EFFECT + ".Buff_Abyss_Phase_014_C"}, "bAddToCharacter": True}],
            }},
        }
        builder.source_row_ids = {("abyss_seasons", "Abyss_14"): 1, ("abyss_buff_configs", "abyss_14_buff_01"): 2,
                                  **{("abyss_buff_curves", key): index for index, key in enumerate(curves, 3)}}
        builder._import_outer_realm_buffs()
        rows = db.execute("SELECT trigger_kind,property_id,property_value,duration_seconds,stack_limit_count,effect_asset_path "
                          "FROM outer_realm_season_buff_component ORDER BY component_ordinal").fetchall()
        assert rows[0] == ("whole_battle", "DefIgnore", .2, None, 1, None)
        assert len(rows) == 7
        assert {row[1] for row in rows[1:]} == {"DamagePenetrate" + name for name in (
            "Cosmos", "Nature", "Incantation", "Chaos", "Psyche", "Lakshana")}
        assert all(row[0] == "observed_recipient_effect" and row[2:] == (.2, 20, 1, EFFECT + "_Penetrate")
                   for row in rows[1:])
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
