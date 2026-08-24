# 验证官方额外形状严格只读发行静态库。

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.services.character_shape_bonus_service import (
    get_effective_character_shape_bonus,
    reset_public_character_shape_bonus,
    save_public_character_shape_bonus,
)
from src.storage.sqlite.shared_data_dao import SharedDataDao
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


PROJECT_DATABASE = Path(__file__).resolve().parents[1] / "data" / "game_static.sqlite3"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CharacterShapeBonusServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.static_database = root / "game_static.sqlite3"
        self.shared_database = root / "app_shared.sqlite3"
        shutil.copy2(PROJECT_DATABASE, self.static_database)
        with closing(sqlite3.connect(self.static_database)) as connection:
            row = connection.execute(
                """SELECT representative_character_id, logical_character_key
                   FROM logical_character_shape_bonus
                   ORDER BY representative_character_id LIMIT 1"""
            ).fetchone()
        if row is None:
            raise AssertionError("发行静态库缺少官方额外形状测试数据")
        self.character_id = int(row[0])
        self.logical_key = str(row[1])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def effective(self):
        with StaticGameDataDao(self.static_database) as static_dao:
            return get_effective_character_shape_bonus(
                static_dao,
                self.character_id,
                shared_database_path=self.shared_database,
            )

    def _write_legacy_override(self) -> None:
        with SharedDataDao(self.shared_database) as shared_dao:
            shared_dao.upsert_shape_bonus_override(
                self.logical_key,
                representative_character_id=self.character_id,
                shape_label="Type-4",
                shape_grid_count=4,
                properties=[{"property_id": "CritBase", "display_value": 8.0}],
                based_on_dataset_id="legacy-fixture",
            )

    def test_effective_value_is_the_static_database_row(self) -> None:
        with StaticGameDataDao(self.static_database) as static_dao:
            expected = static_dao.get_character_shape_bonus(self.character_id)
        effective = self.effective()

        self.assertEqual("static_default", effective["effective_source"])
        self.assertEqual(expected["shape_label"], effective["shape_label"])
        self.assertEqual(expected["properties"], effective["properties"])

    def test_legacy_shared_override_is_ignored(self) -> None:
        expected = self.effective()
        self._write_legacy_override()

        actual = self.effective()

        self.assertEqual(expected, actual)
        self.assertNotEqual("Type-4", actual["shape_label"])

    def test_official_shape_save_is_rejected_without_writing_either_database(self) -> None:
        static_hash = _sha256(self.static_database)

        with self.assertRaisesRegex(ValueError, "静态资源库管理，不可编辑"):
            save_public_character_shape_bonus(
                self.character_id,
                shape_label="Type-4",
                property_values={"CritBase": 8.0},
                database_path=self.static_database,
                shared_database_path=self.shared_database,
            )

        self.assertEqual(static_hash, _sha256(self.static_database))
        self.assertFalse(self.shared_database.exists())

    def test_legacy_override_cleanup_returns_static_value(self) -> None:
        self._write_legacy_override()

        restored = reset_public_character_shape_bonus(
            self.character_id,
            database_path=self.static_database,
            shared_database_path=self.shared_database,
        )

        self.assertEqual("static_default", restored["effective_source"])
        with SharedDataDao(self.shared_database) as shared_dao:
            self.assertIsNone(shared_dao.get_shape_bonus_override(self.logical_key))


if __name__ == "__main__":
    unittest.main()
