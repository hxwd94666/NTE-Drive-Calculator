# 测试静态数据库连接复用与 PRAGMA 优化。
"""Unit tests verifying SQLite static DB connection reuse and PRAGMAs."""

import tempfile
import threading
import unittest
from pathlib import Path

from src.storage.sqlite.static_game_data_dao import (
    StaticGameDataDao,
    _SHARED_STATIC_CONNECTIONS,
)

STATIC_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "game_static.sqlite3"


class StaticStoragePerfTests(unittest.TestCase):
    def setUp(self):
        StaticGameDataDao.close_shared_connections()

    def tearDown(self):
        StaticGameDataDao.close_shared_connections()

    def test_static_connection_reuse_across_instances(self):
        """Creating multiple DAOs on the static DB reuses the same connection."""
        dao1 = StaticGameDataDao(STATIC_DB_PATH)
        dao2 = StaticGameDataDao(STATIC_DB_PATH)

        self.assertIs(dao1._connection, dao2._connection)

        # Querying through one works through both
        res1 = dao1.character_templates() if hasattr(dao1, "character_templates") else dao1.summary()
        res2 = dao2.character_templates() if hasattr(dao2, "character_templates") else dao2.summary()
        self.assertIsNotNone(res1)
        self.assertIsNotNone(res2)

        # Closing without force leaves shared connection open
        dao1.close()
        self.assertIsNotNone(dao2._connection)
        self.assertTrue(len(_SHARED_STATIC_CONNECTIONS) > 0)

        # Force close or close_shared_connections properly cleans up
        StaticGameDataDao.close_shared_connections()
        self.assertEqual(len(_SHARED_STATIC_CONNECTIONS), 0)

    def test_pragmas_applied(self):
        """PRAGMAs cache_size, mmap_size, temp_store are set."""
        dao = StaticGameDataDao(STATIC_DB_PATH)
        conn = dao._connection

        cache_size = conn.execute("PRAGMA cache_size").fetchone()[0]
        self.assertEqual(cache_size, -32000)

        mmap_size = conn.execute("PRAGMA mmap_size").fetchone()[0]
        self.assertGreaterEqual(mmap_size, 268435456)

        temp_store = conn.execute("PRAGMA temp_store").fetchone()[0]
        # 2 = MEMORY
        self.assertEqual(temp_store, 2)

    def test_temp_database_not_reused_by_default(self):
        """Databases in temporary directories do not reuse connections by default."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_db = Path(tmp_dir) / "test.sqlite3"
            # Copy static DB to temp DB
            import shutil
            shutil.copy2(STATIC_DB_PATH, temp_db)

            dao1 = StaticGameDataDao(temp_db)
            dao2 = StaticGameDataDao(temp_db)
            try:
                self.assertIsNot(dao1._connection, dao2._connection)
            finally:
                dao1.close()
                dao2.close()

    def test_concurrent_multi_threaded_queries(self):
        """Shared static connection handles concurrent queries across threads reliably."""
        errors = []

        def worker():
            try:
                for _ in range(25):
                    dao = StaticGameDataDao(STATIC_DB_PATH)
                    dao.summary()
                    dao.close()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent queries failed with errors: {errors}")
