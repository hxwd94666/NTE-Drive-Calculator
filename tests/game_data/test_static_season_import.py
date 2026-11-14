# 验证赛季属性、曲线与触发条件的导入证据，不将条件效果当常驻。


# 验证召唤赛季只保存召唤限定规则，并兼容既有赛季迁移。
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from tools.game_data.static_database_encounter_imports import EncounterImportMixin

ROOT = Path(__file__).resolve().parents[2]


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

# 验证同版深渊曲线同时可供赛季表和公共 Buff 求值读取。
import sqlite3
import unittest

from tools.game_data.static_database_catalog_imports import CatalogImportMixin


class AbyssCurveImportTests(unittest.TestCase):
    def test_abyss_curve_keeps_official_path_and_missing_role_scope_is_allowed(self):
        class Probe(CatalogImportMixin):
            character_effect_curve_rows = ()

            def source_row_id(self, table, row):
                return 1

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.executescript("""
            CREATE TABLE combat_curve(path,id,interpolation,default_value,before,after,source);
            CREATE TABLE combat_curve_point(path,id,ordinal,time,value);
        """)
        probe = Probe()
        probe.connection = conn
        probe.rows = {"abyss_buff_curves": {"test": {
            "Keys": [{"Time": 1, "Value": 0.45}],
            "InterpMode": "RCIM_Constant", "PreInfinityExtrap": "RCCE_Constant",
            "PostInfinityExtrap": "RCCE_Constant",
        }}}
        probe._import_combat_curves()
        self.assertEqual(conn.execute("SELECT path,id,time,value FROM combat_curve_point").fetchone(),
                         ("/Game/DataAssets/DataAssetSet/Abyss/CT_AbyssBuff", "test", 1.0, 0.45))
        probe.rows = {}
        probe._import_combat_curves()
        self.assertEqual(conn.execute("SELECT count(*) FROM combat_curve_point").fetchone()[0], 1)
