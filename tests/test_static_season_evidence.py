# 验证赛季排期补注、静态迁移和新模型证据边界。
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

from tools.game_data.static_database_encounter_imports import EncounterImportMixin
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.services.battle_report.environment.outer_realm_buffs import (
    BattleOuterRealmBuffComponent, BattleOuterRealmBuffConfig, BattleOuterRealmBuffService,
)


class StaticSeasonEvidenceTests(unittest.TestCase):
    def test_dao_and_loader_preserve_component_source_and_legacy_unknown(self):
        schema = Path(__file__).resolve().parents[1] / "src/storage/sqlite/schema"
        effect = "/Game/Fixture/SeasonEffect"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "static.sqlite3"
            # 仅构造正式读取链所需关系；赛季表和新增来源列直接使用已发布迁移。
            with closing(sqlite3.connect(path)) as db:
                db.executescript("""
                    CREATE TABLE schema_migration(version INTEGER PRIMARY KEY);
                    INSERT INTO schema_migration VALUES(36);
                    CREATE TABLE source_row(source_row_id INTEGER PRIMARY KEY);
                    INSERT INTO source_row VALUES(1);
                    CREATE TABLE outer_realm_rotation(level_config_id TEXT PRIMARY KEY);
                    INSERT INTO outer_realm_rotation VALUES('Abyss_10');
                    CREATE TABLE abyss_level_monster_spawn(
                        level_config_id TEXT, level_id INTEGER, fight_stage TEXT, monster_pool_id TEXT);
                    CREATE TABLE abyss_monster_pool_entry(
                        monster_pool_id TEXT, attribute_profile_set TEXT, attribute_pack_id TEXT);
                    CREATE TABLE enemy_combat_profile(
                        profile_set TEXT, pack_id TEXT, topple_limit REAL, topple_reduce_reset REAL);
                """)
                db.executescript((schema / "026_game_static_outer_realm_buff.sql").read_text(encoding="utf-8"))
                db.execute("INSERT INTO outer_realm_season_buff VALUES(?,?,?,?,?,?,?,?,?)",
                           ("Abyss_10", "测试赛季", "season", "测试", "测试", "/Game/Fixture/Season", 1, 1, 1))
                db.executemany("INSERT INTO outer_realm_season_buff_component VALUES(?,?,?,?,?,?,?,?,?,?)", [
                    ("Abyss_10", 0, "whole_battle", "DamageUpGeneralBase", .3, None, None, 1, "base", 1),
                    ("Abyss_10", 1, "while_target_toppled", "AtkUp", .25, None, None, 1, "attack", 1),
                ])
                db.commit()
            for version in (36, 37):
                if version == 37:
                    with closing(sqlite3.connect(path)) as db:
                        db.executescript((schema / "037_game_static_season_evidence.sql").read_text(encoding="utf-8"))
                        db.execute("INSERT INTO schema_migration VALUES(37)")
                        db.execute("UPDATE outer_realm_season_buff_component SET trigger_kind='whole_battle_qte' WHERE component_ordinal=0")
                        db.execute("UPDATE outer_realm_season_buff_component SET trigger_kind='observed_recipient_effect', effect_asset_path=? WHERE component_ordinal=1", (effect,))
                        db.commit()
                expected = [None, effect if version == 37 else None]
                before = sha256(path.read_bytes()).hexdigest()
                try:
                    with StaticGameDataDao(path) as dao:
                        self.assertEqual(version, dao.schema_version)
                        row = dao.get_outer_realm_season_buff("Abyss_10")
                    with self.subTest(version=version, projection="dao"):
                        self.assertEqual(expected, [item.get("effect_asset_path") for item in row["components"]])
                    config = BattleOuterRealmBuffService.load(path, "Abyss_10|1|EAbyssFightStage::Up")
                    with self.subTest(version=version, projection="load"):
                        self.assertEqual(expected, [item.effect_asset_path for item in config.components])
                        self.assertEqual([(0, "DamageUpGeneralBase", .3), (1, "AtkUp", .25)],
                                         [(item.component_ordinal, item.property_id, item.value) for item in config.components])
                        self.assertIsNone(config.topple_limit)
                        self.assertIsNone(config.topple_recovery_speed)
                finally:
                    # 旧 schema/新 schema 的未知来源都不猜测，正式只读入口也不晋升或回写静态库。
                    self.assertEqual(before, sha256(path.read_bytes()).hexdigest())

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
