# 培养指南明确无推荐弧盘时不造悬空关系，未知正式 ID 仍阻止构建。
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import unittest

from tools.game_data.static_database_catalog_imports import CatalogImportMixin
from tools.game_data.static_database_build_support import StaticDatabaseError

ROOT = Path(__file__).resolve().parents[2]


class OptionalCultivationForkTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
            CREATE TABLE character(character_id INTEGER PRIMARY KEY);
            CREATE TABLE source_row(source_row_id INTEGER PRIMARY KEY);
            CREATE TABLE fork_item(fork_id TEXT PRIMARY KEY);
            CREATE TABLE equipment_item(item_id TEXT PRIMARY KEY);
            INSERT INTO character VALUES(1074);
            INSERT INTO source_row VALUES(1);
            INSERT INTO fork_item VALUES('fork_confirmed');
        """)
        self.connection.executescript((ROOT / "src/storage/sqlite/schema/017_game_static_combat_catalog.sql").read_text(encoding="utf-8"))

    def builder(self, fork_id):
        return SimpleNamespace(
            connection=self.connection,
            rows={"cultivation_guides": {"1074": {"RecommendForkList": [
                {"ForkItemId": fork_id}, {"ForkItemId": "fork_confirmed"},
            ]}}, "fork_items": {"fork_confirmed": {}}},
            source_row_id=lambda *_: 1,
        )

    def test_explicit_none_keeps_guide_without_fabricating_recommendation(self):
        builder = self.builder("None")
        CatalogImportMixin._import_cultivation_guides(builder)
        self.assertEqual(self.connection.execute("SELECT character_id FROM character_cultivation_guide").fetchall(), [(1074,)])
        self.assertEqual(self.connection.execute("SELECT ordinal,fork_id FROM character_cultivation_fork_recommendation").fetchall(), [(1, "fork_confirmed")])
        self.assertEqual(builder.rows["cultivation_guides"]["1074"]["RecommendForkList"][0]["ForkItemId"], "None")
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_unknown_id_is_not_treated_as_explicit_none(self):
        with self.assertRaises((StaticDatabaseError, sqlite3.IntegrityError)):
            with self.connection:
                CatalogImportMixin._import_cultivation_guides(self.builder("fork_missing"))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM character_cultivation_guide").fetchone()[0], 0)
