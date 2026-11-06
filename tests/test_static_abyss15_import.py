# 验证召唤赛季只保存召唤限定规则，并兼容既有赛季迁移。
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from tools.game_data.static_database_encounter_imports import EncounterImportMixin

ROOT = Path(__file__).resolve().parents[1]


def test_summon_season_uses_formal_curve_and_does_not_become_unconditional():
    with sqlite3.connect(":memory:") as db:
        db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE source_row(source_row_id INTEGER PRIMARY KEY);
            INSERT INTO source_row VALUES(1),(2),(3);
            CREATE TABLE outer_realm_rotation(level_config_id TEXT PRIMARY KEY,inference_ordinal INTEGER);
            INSERT INTO outer_realm_rotation VALUES('Abyss_15',15);
        """)
        for file in ("022_game_static_encounter_catalog.sql", "026_game_static_outer_realm_buff.sql", "037_game_static_season_evidence.sql", "040_game_static_summon_season.sql"):
            db.executescript((ROOT / "src/storage/sqlite/schema" / file).read_text(encoding="utf-8"))
        builder = SimpleNamespace(
            connection=db,
            rows={
                "abyss_buff_curves": {"Buff_Abyss_Phase_015_SummonDmgUp": {"Keys": [{"Time": 1, "Value": .45}]}},
                "abyss_seasons": {"Abyss_15": {"BuffID": "abyss_15_buff_01", "SeasonName": {"LocalizedString": "赛季"}}},
                "abyss_buff_configs": {"abyss_15_buff_01": {"BuffName": {"LocalizedString": "召唤限定"}, "BuffDesc": {"LocalizedString": "描述"},
                    "CloneBuffArray": [{"BuffGE": {"AssetPathName": "/Game/Buff_Abyss_Phase_015.Buff_Abyss_Phase_015_C"}, "bAddToCharacter": True}]}},
            },
            source_row_id=lambda *_: 1,
        )
        EncounterImportMixin._import_outer_realm_buffs(builder)
        assert db.execute("SELECT trigger_kind,property_id,property_value,duration_seconds,stack_limit_count FROM outer_realm_season_buff_component").fetchall() == [
            ("whole_battle_summon", "DamageUpGeneralBase", .45, None, 1)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
