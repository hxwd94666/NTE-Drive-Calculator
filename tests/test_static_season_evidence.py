# 验证赛季排期补注、静态迁移和新模型证据边界。
import re
import sqlite3
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from tools.game_data.static_database_encounter_imports import EncounterImportMixin
from src.services.battle_outer_realm_buff_service import (
    BattleOuterRealmBuffComponent, BattleOuterRealmBuffConfig, BattleOuterRealmBuffService,
)


class StaticSeasonEvidenceTests(unittest.TestCase):
    def test_v37_preserves_old_components_and_rejects_unknown_trigger(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE source_row(source_row_id INTEGER PRIMARY KEY)")
        db.execute("CREATE TABLE outer_realm_rotation(level_config_id TEXT PRIMARY KEY)")
        schema = Path(__file__).resolve().parents[1] / "src/storage/sqlite/schema"
        db.executescript((schema / "026_game_static_outer_realm_buff.sql").read_text(encoding="utf-8"))
        row = ("Abyss_9", 0, "whole_battle", "DamageUpNatureBase", .3, None, None, 1, "curve", 1)
        db.execute("INSERT INTO outer_realm_season_buff_component VALUES (?,?,?,?,?,?,?,?,?,?)", row)
        db.executescript((schema / "037_game_static_season_evidence.sql").read_text(encoding="utf-8"))
        self.assertEqual(db.execute("SELECT * FROM outer_realm_season_buff_component").fetchone(), (*row, None))
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE outer_realm_season_buff_component SET trigger_kind='guessed_team_buff'")
        db.close()

    def test_confirmed_gap_is_annotated_and_different_dates_are_not_inferred(self):
        for end, expected in [("2026.09.18-04.59.59", True), ("2026.09.17-04.59.59", False)]:
            with self.subTest(end=end):
                db = sqlite3.connect(":memory:")
                db.execute("CREATE TABLE outer_realm_rotation(level_config_id TEXT PRIMARY KEY, starts_at TEXT, ends_at TEXT, inference_ordinal INTEGER, source_row_id INTEGER)")
                db.execute("CREATE TABLE outer_realm_rotation_annotation(level_config_id TEXT, basis TEXT, predecessor_id TEXT, successor_id TEXT)")
                def quest(identity, start, finish):
                    def timestamp(value):
                        return {"MainlandTime": dict(zip(("Year", "Month", "Day", "Hour", "minute", "Second"), map(int, re.split(r"[.\-]", value))))}
                    return {"bTimeLimitQuest": True, "ObjectiveInfo": {"ObjectiveType": "AbyssCompleted", "AbyssID": identity}, "QuestStartTime": timestamp(start), "QuestEndTime": timestamp(finish)}
                source = SimpleNamespace(connection=db, as_of=date(2026, 9, 24), source_row_id=lambda *args: 1,
                    rows={"abyss_seasons": {"Abyss_10": {}}, "combat_award_quests": {
                        "nine": quest("Abyss_9", "2026.09.04-05.00.00", end),
                        "eleven": quest("Abyss_11", "2026.10.02-05.00.00", "2026.10.16-04.59.59"),
                    }})
                EncounterImportMixin._import_outer_realm_rotations(source)
                row = db.execute("SELECT starts_at,ends_at,inference_ordinal FROM outer_realm_rotation WHERE level_config_id='Abyss_10'").fetchone()
                if expected:
                    self.assertEqual(row, ("2026-09-18T05:00:00", "2026-10-02T04:59:59", 0))
                    self.assertEqual(db.execute("SELECT predecessor_id,successor_id FROM outer_realm_rotation_annotation").fetchone(), ("Abyss_9", "Abyss_11"))
                else:
                    self.assertIsNone(row)
                    self.assertEqual(db.execute("SELECT count(*) FROM outer_realm_rotation_annotation").fetchone()[0], 0)
                db.close()

    def test_legacy_inference_does_not_silently_drop_new_season(self):
        config = BattleOuterRealmBuffConfig(level_config_id="Abyss_11", season_name="测试", buff_id="season",
            buff_name="测试", description="测试", gameplay_effect_path="/Game/Season",
            components=(BattleOuterRealmBuffComponent(component_ordinal=0, trigger_kind="observed_recipient_effect",
                property_id="DamageUpGeneralBase", value=.4, effect_asset_path="/Game/Effect"),))
        with self.assertRaisesRegex(ValueError, "逐击角色证据"):
            BattleOuterRealmBuffService.infer(config, hits=(), battle_end_us=1)
