# 测试本机公共覆盖数据库的结构迁移、事务与生命周期。

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.storage.sqlite.shared_data_dao import (
    MIGRATIONS,
    SCHEMA_VERSION,
    SharedDataDao,
    SharedDataError,
)


class SharedDataDaoTests(unittest.TestCase):
    def test_new_database_has_current_schema_and_cascade_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "app_shared.sqlite3"
            with SharedDataDao(database) as dao:
                saved = dao.upsert_shape_bonus_override(
                    "character:1003",
                    representative_character_id=1003,
                    shape_label="Type-3",
                    shape_grid_count=3,
                    properties=[
                        {"property_id": "AtkUp", "display_value": 10.0}
                    ],
                    based_on_dataset_id="fixture",
                )
                self.assertEqual("shared_override", saved["source_kind"])
                self.assertTrue(
                    dao.delete_shape_bonus_override("character:1003")
                )
                self.assertIsNone(
                    dao.get_shape_bonus_override("character:1003")
                )
            with closing(sqlite3.connect(database)) as connection:
                version = connection.execute(
                    "SELECT MAX(version) FROM schema_migration"
                ).fetchone()[0]
                property_count = connection.execute(
                    """SELECT COUNT(*)
                       FROM logical_character_shape_bonus_property_override"""
                ).fetchone()[0]

        self.assertEqual(SCHEMA_VERSION, version)
        self.assertEqual(0, property_count)

    def test_version_one_database_migrates_to_current_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "app_shared.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                with connection:
                    connection.execute(
                        """CREATE TABLE schema_migration (
                               version INTEGER PRIMARY KEY,
                               applied_at_utc TEXT NOT NULL
                           )"""
                    )
                    connection.executescript(
                        MIGRATIONS[1].read_text(encoding="utf-8")
                    )
                    connection.execute(
                        """INSERT INTO database_profile
                           VALUES (1, 'app_shared', 'before', 'before')"""
                    )
                    connection.execute(
                        "INSERT INTO schema_migration VALUES (1, 'before')"
                    )
            with SharedDataDao(database) as dao:
                self.assertFalse(
                    dao.migration_completed("legacy_static_shape_bonus_v1")
                )
            with closing(sqlite3.connect(database)) as connection:
                version = connection.execute(
                    "SELECT MAX(version) FROM schema_migration"
                ).fetchone()[0]

        self.assertEqual(SCHEMA_VERSION, version)

    def test_failed_property_write_rolls_back_parent_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "app_shared.sqlite3"
            with SharedDataDao(database) as dao:
                dao._db().execute(
                    """CREATE TRIGGER reject_property
                       BEFORE INSERT ON
                           logical_character_shape_bonus_property_override
                       BEGIN
                           SELECT RAISE(ABORT, 'fixture rejection');
                       END"""
                )
                with self.assertRaisesRegex(
                    SharedDataError,
                    "无法保存公共额外形状覆盖",
                ):
                    dao.upsert_shape_bonus_override(
                        "character:1003",
                        representative_character_id=1003,
                        shape_label="Type-3",
                        shape_grid_count=3,
                        properties=[
                            {"property_id": "AtkUp", "display_value": 10.0}
                        ],
                    )
                self.assertIsNone(
                    dao.get_shape_bonus_override("character:1003")
                )

    def test_closed_dao_rejects_further_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dao = SharedDataDao(Path(temporary) / "app_shared.sqlite3")
            dao.close()

            with self.assertRaisesRegex(SharedDataError, "已关闭"):
                dao.get_shape_bonus_override("character:1003")


if __name__ == "__main__":
    unittest.main()
