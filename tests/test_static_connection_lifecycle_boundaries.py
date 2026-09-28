# 校验静态库共享连接在并发与退出路径上的生命周期约束。
"""Regression guards for the shared static read-only connection lifecycle.

背景：共享连接以 ``check_same_thread=False`` 跨线程复用，此前 Python 侧没有
任何保护，且 ``close_shared_connections()`` 在生产代码里没有调用点。SQLite
本身是 serialized 模式（``sqlite3.threadsafety == 3``），并发 execute 安全，
真正的风险是「查询线程拿到已被另一线程关闭的连接」与「退出时连接一直留到
解释器结束」。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from src.storage.sqlite.static_game_data_dao import (
    StaticGameDataDao,
    StaticGameDataError,
)

ROOT = Path(__file__).resolve().parents[1]
STATIC_DB_PATH = ROOT / "data" / "game_static.sqlite3"


class StaticConnectionLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        StaticGameDataDao.close_shared_connections()

    def tearDown(self) -> None:
        StaticGameDataDao.close_shared_connections()

    def test_forced_close_surfaces_a_domain_error(self) -> None:
        """强制关闭共享连接后，残留句柄的查询必须转为领域错误。"""

        if not STATIC_DB_PATH.is_file():
            self.skipTest("缺少静态库测试数据")
        survivor = StaticGameDataDao(STATIC_DB_PATH)
        closer = StaticGameDataDao(STATIC_DB_PATH)
        closer.close(force=True)

        with self.assertRaises(StaticGameDataError):
            survivor._rows("SELECT character_id FROM character LIMIT 1")

    def test_application_close_event_reclaims_shared_connections(self) -> None:
        """应用退出（MainWindow.closeEvent）必须回收共享连接。"""

        source = (ROOT / "src" / "ui" / "app.py").read_text(encoding="utf-8")
        start = source.index("    def closeEvent(self, e):")
        end = source.index("super().closeEvent(e)", start)
        self.assertIn("close_shared_connections(", source[start:end])


if __name__ == "__main__":
    unittest.main()
