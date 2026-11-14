# 验证官方明确空弧盘推荐不伪造关联，缺项和无效正式身份仍阻止构建。
from pathlib import Path
import sqlite3
import unittest

from tools.game_data.static_database_catalog_imports import CatalogImportMixin
from tools.game_data.static_database_build_support import StaticDatabaseError


class Builder(CatalogImportMixin):
    def source_row_id(self, _table, _key):
        return 1


class CultivationEmptyForkTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.connection.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE character(character_id INTEGER PRIMARY KEY);
            CREATE TABLE source_row(source_row_id INTEGER PRIMARY KEY);
            CREATE TABLE fork_item(fork_id TEXT PRIMARY KEY);
            INSERT INTO character VALUES(1074);
            INSERT INTO source_row VALUES(1);
            INSERT INTO fork_item VALUES('known_fork');
        """)
        schema = Path(__file__).resolve().parents[2] / "src/storage/sqlite/schema/017_game_static_combat_catalog.sql"
        self.connection.executescript(schema.read_text(encoding="utf-8"))
        self.builder = Builder()
        self.builder.connection = self.connection

    def import_rows(self, recommendations):
        self.builder.rows = {"cultivation_guides": {"1074": {"RecommendForkList": recommendations}}}
        self.builder._import_cultivation_guides()

    def test_explicit_none_keeps_guide_and_other_recommendation_identity(self):
        self.import_rows([{"ForkItemId": "None"}, {"ForkItemId": "known_fork"}])
        self.assertEqual(self.connection.execute("SELECT character_id FROM character_cultivation_guide").fetchall(), [(1074,)])
        self.assertEqual(self.connection.execute("SELECT ordinal,fork_id FROM character_cultivation_fork_recommendation").fetchall(), [(1, "known_fork")])

    def test_missing_identity_is_not_explicit_none(self):
        with self.assertRaises(StaticDatabaseError):
            self.import_rows([{}])

    def test_unknown_nonempty_identity_still_fails_foreign_key(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.import_rows([{"ForkItemId": "missing_fork"}])
