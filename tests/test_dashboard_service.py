# 测试首页聚合服务对静态数据和账号数据的只读查询。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.services.dashboard_service import DashboardService
from src.storage.sqlite.user_data_dao import UserDataDao


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def item(serial: int) -> dict:
    return {
        "uid": {"slot": 2, "serial": serial},
        "kind": "module",
        "item_id": f"module-{serial}",
        "level": 1,
        "max_level": 20,
        "locked": False,
        "equipped": False,
        "main_stats": [],
        "sub_stats": [],
    }


def snapshot(sequence: int, items: list[dict]) -> dict:
    return {
        "complete": True,
        "item_count": len(items),
        "items": items,
        "generation": 1,
        "sequence": sequence,
    }


class DashboardServiceTests(unittest.TestCase):
    def test_aggregates_account_static_data_and_recent_inventory_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            user_database = Path(temporary) / "user_data.sqlite3"
            with UserDataDao(
                user_database,
                account_id="dashboard-test",
                account_name="工作台测试",
            ) as dao:
                first = dao.import_inventory_snapshot(snapshot(1, [item(1)]))
                second = dao.import_inventory_snapshot(snapshot(2, [item(1), item(2)]))

            dashboard = DashboardService(
                user_database,
                static_database_path=PROJECT_ROOT / "data" / "game_static.sqlite3",
            ).load()

            self.assertEqual("工作台测试", dashboard["account"]["account_name"])
            self.assertEqual(second, dashboard["inventory"]["snapshot_id"])
            self.assertEqual(2, dashboard["inventory"]["stored_item_count"])
            self.assertEqual(1, dashboard["recent_change"]["added_count"])
            self.assertEqual(first, dashboard["recent_change"]["before_snapshot_id"])
            self.assertGreater(dashboard["static"]["counts"]["character"], 0)


if __name__ == "__main__":
    unittest.main()
