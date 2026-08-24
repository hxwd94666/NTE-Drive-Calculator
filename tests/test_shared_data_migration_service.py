# 测试旧版静态库的额外形状差异只迁移一次且不污染新版静态库。

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
)
from src.services.shared_data_migration_service import (
    MIGRATION_KEY,
    migrate_legacy_static_shape_bonuses,
)
from src.storage.sqlite.shared_data_dao import SharedDataDao
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


ROOT = Path(__file__).resolve().parents[1]
PROJECT_DATABASE = ROOT / "data" / "game_static.sqlite3"
BASELINE = ROOT / "data" / "migrations" / "shape_bonus_defaults_2.0.2.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SharedDataMigrationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.legacy = root / "migration" / "game_static.previous.sqlite3"
        self.current = root / "current" / "game_static.sqlite3"
        self.shared = root / "app_shared.sqlite3"
        self.legacy.parent.mkdir(parents=True)
        self.current.parent.mkdir(parents=True)
        shutil.copy2(PROJECT_DATABASE, self.legacy)
        shutil.copy2(PROJECT_DATABASE, self.current)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def migrate(self):
        return migrate_legacy_static_shape_bonuses(
            legacy_database_path=self.legacy,
            current_static_database_path=self.current,
            shared_database_path=self.shared,
            baseline_path=BASELINE,
        )

    def test_pristine_old_default_does_not_become_permanent_override(self) -> None:
        first = self.migrate()
        second = self.migrate()

        self.assertEqual("completed", first["status"])
        self.assertEqual(0, first["migrated_count"])
        self.assertEqual("already_completed", second["status"])
        with SharedDataDao(self.shared) as shared_dao:
            self.assertTrue(shared_dao.migration_completed(MIGRATION_KEY))
            self.assertIsNone(
                shared_dao.get_shape_bonus_override("character:1003")
            )

    def test_migrated_old_difference_is_retained_but_not_effective(self) -> None:
        with closing(sqlite3.connect(self.legacy)) as connection:
            with connection:
                connection.execute(
                    """UPDATE logical_character_shape_bonus
                       SET shape_label = 'Type-4', shape_grid_count = 4
                       WHERE logical_character_key = 'character:1003'"""
                )
                connection.execute(
                    """UPDATE logical_character_shape_bonus_property
                       SET property_id = 'CritBase', display_value = 8.0
                       WHERE logical_character_key = 'character:1003'"""
                )
        with closing(sqlite3.connect(self.current)) as connection:
            with connection:
                connection.execute(
                    """UPDATE logical_character_shape_bonus
                       SET shape_label = 'Type-2', shape_grid_count = 2
                       WHERE logical_character_key = 'character:1003'"""
                )
        current_hash = _sha256(self.current)

        result = self.migrate()
        with StaticGameDataDao(self.current) as static_dao:
            effective = get_effective_character_shape_bonus(
                static_dao,
                1003,
                shared_database_path=self.shared,
            )
            new_default = static_dao.get_character_shape_bonus(1003)

        self.assertEqual(1, result["migrated_count"])
        self.assertEqual("Type-2", effective["shape_label"])
        self.assertEqual("static_default", effective["effective_source"])
        self.assertEqual("Type-2", new_default["shape_label"])
        with SharedDataDao(self.shared) as shared_dao:
            legacy = shared_dao.get_shape_bonus_override("character:1003")
        self.assertEqual("Type-4", legacy["shape_label"])
        self.assertEqual(current_hash, _sha256(self.current))

    def test_failed_mapping_keeps_backup_and_does_not_mark_migration(self) -> None:
        with closing(sqlite3.connect(self.legacy)) as connection:
            with connection:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    """UPDATE logical_character_shape_bonus_property
                       SET property_id = 'RemovedProperty', display_value = 8.0
                       WHERE logical_character_key = 'character:1003'"""
                )
        current_hash = _sha256(self.current)

        with self.assertRaisesRegex(RuntimeError, "无法映射"):
            self.migrate()

        self.assertTrue(self.legacy.is_file())
        self.assertEqual(current_hash, _sha256(self.current))
        with SharedDataDao(self.shared) as shared_dao:
            self.assertFalse(shared_dao.migration_completed(MIGRATION_KEY))


if __name__ == "__main__":
    unittest.main()
