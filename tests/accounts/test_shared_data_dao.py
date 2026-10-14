# 测试本机公共覆盖数据库的结构迁移、事务与生命周期。

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from threading import Barrier, Thread
from unittest.mock import patch

from src.storage.sqlite.shared_data_dao import (
    MIGRATIONS,
    SCHEMA_VERSION,
    SharedDataDao,
    SharedDataError,
)


class SharedDataDaoTests(unittest.TestCase):
    @staticmethod
    def _version_one_database(database: Path) -> None:
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                "CREATE TABLE schema_migration (version INTEGER PRIMARY KEY, applied_at_utc TEXT NOT NULL)"
            )
            connection.executescript(MIGRATIONS[1].read_text(encoding="utf-8"))
            connection.execute("INSERT INTO database_profile VALUES (1, 'app_shared', 'before', 'before')")
            connection.execute("INSERT INTO schema_migration VALUES (1, 'before')")

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
            self._version_one_database(database)
            with SharedDataDao(database) as dao:
                self.assertFalse(
                    dao.migration_completed("legacy_static_shape_bonus_v1")
                )
            with closing(sqlite3.connect(database)) as connection:
                version = connection.execute(
                    "SELECT MAX(version) FROM schema_migration"
                ).fetchone()[0]

        self.assertEqual(SCHEMA_VERSION, version)

    def test_marker_failure_keeps_original_schema_and_values_then_retries(self) -> None:
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as temporary:
                database = Path(temporary) / "app_shared.sqlite3"
                if existing:
                    self._version_one_database(database)
                with closing(sqlite3.connect(database)) as connection, connection:
                    if existing:
                        connection.execute(
                            "INSERT INTO logical_character_shape_bonus_override VALUES (?, ?, ?, ?, ?, ?)",
                            ("synthetic", 1, "Type-3", 3, "fixture", "before"),
                        )
                        connection.execute(
                            "INSERT INTO logical_character_shape_bonus_property_override VALUES (?, ?, ?, ?)",
                            ("synthetic", "AtkUp", 7.0, 0),
                        )
                with closing(sqlite3.connect(database)) as connection:
                    before = list(connection.iterdump())
                # 不改发布 SQL；仅隔离候选在 DDL 完成后以真实触发器拒绝版本标记。
                failed_script = Path(temporary) / "marker_failure.sql"
                failed_script.write_text(
                    MIGRATIONS[2].read_text(encoding="utf-8")
                    + "\nCREATE TRIGGER reject_marker BEFORE INSERT ON schema_migration "
                    "WHEN NEW.version=2 BEGIN SELECT RAISE(ABORT, 'synthetic marker failure'); END;\n",
                    encoding="utf-8",
                )
                with patch.dict(MIGRATIONS, {2: failed_script}), self.assertRaises(SharedDataError):
                    SharedDataDao(database)
                with closing(sqlite3.connect(database)) as connection:
                    self.assertEqual(before, list(connection.iterdump()))
                    self.assertEqual("ok", connection.execute("PRAGMA integrity_check").fetchone()[0])
                with SharedDataDao(database) as dao:
                    self.assertFalse(dao.migration_completed("unused"))
                    if existing:
                        saved = dao.get_shape_bonus_override("synthetic")
                        self.assertEqual(7.0, saved["properties"][0]["display_value"])

    def test_concurrent_open_reads_version_after_acquiring_write_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "app_shared.sqlite3"
            self._version_one_database(database)
            barrier, errors, completed = Barrier(2), [], []
            real_connect = sqlite3.connect

            class OverlappingConnection(sqlite3.Connection):
                waited = False

                def execute(self, sql, parameters=()):
                    if sql == "BEGIN IMMEDIATE" and not self.waited:
                        self.waited = True
                        # 两个真实连接先同时到达写锁入口，不用 sleep 或重试制造交错。
                        barrier.wait(3)
                    return super().execute(sql, parameters)

            def open_database():
                try:
                    with SharedDataDao(database) as dao:
                        completed.append(not dao.migration_completed("unused"))
                except Exception as error:
                    errors.append(error)

            def connect(*args, **kwargs):
                return real_connect(*args, **kwargs, factory=OverlappingConnection)

            workers = [Thread(target=open_database) for _ in range(2)]
            with patch("src.storage.sqlite.shared_data_dao.sqlite3.connect", connect):
                try:
                    for worker in workers:
                        worker.start()
                finally:
                    for worker in workers:
                        worker.join(15)
                        self.assertFalse(worker.is_alive())
            self.assertEqual([], errors)
            self.assertEqual([True, True], completed)

    def test_current_schema_read_does_not_wait_for_reserved_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "app_shared.sqlite3"
            with SharedDataDao(database) as dao:
                dao.upsert_shape_bonus_override(
                    "synthetic", representative_character_id=1, shape_label="Type-3",
                    shape_grid_count=3, properties=[{"property_id": "AtkUp", "display_value": 7.0}],
                )
            real_connect = sqlite3.connect

            class BoundedConnection(sqlite3.Connection):
                def execute(self, sql, parameters=()):
                    if sql == "PRAGMA busy_timeout = 10000":
                        sql = "PRAGMA busy_timeout = 50"
                    return super().execute(sql, parameters)

            def connect(*args, **kwargs):
                return real_connect(*args, **kwargs, factory=BoundedConnection)

            with closing(real_connect(database)) as writer:
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("UPDATE logical_character_shape_bonus_property_override SET display_value = 99")
                try:
                    with patch("src.storage.sqlite.shared_data_dao.sqlite3.connect", connect):
                        with SharedDataDao(database) as dao:
                            self.assertEqual(7.0, dao.get_shape_bonus_override("synthetic")["properties"][0]["display_value"])
                finally:
                    writer.rollback()

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


if __name__ == "__main__":
    unittest.main()
