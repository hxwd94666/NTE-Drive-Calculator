# 验证配装方案批量去激活的原子性：非法输入不得留下部分已生效的持久状态。
"""批量去激活要么全部生效，要么完全不改；调用方无需猜测哪些方案已生效。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError
from tests.test_allocation_lock_service import _core, _module, _official_payload
from tests.test_user_data_loadout_dao import item, snapshot


class LoadoutPlanBatchDeactivateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.dao = UserDataDao(
            Path(self.temporary.name) / "user.sqlite3", account_id="batch",
        )
        self.addCleanup(self.dao.close)
        self.snapshot_id = self.dao.import_inventory_snapshot(
            snapshot(1, [item(1, 2), item(3, 4, kind="core")]),
        )

    def _save_plan(self, role_name: str, character_id: int) -> int:
        return self.dao.save_loadout_plan(
            name=f"{role_name} 方案",
            character_id=character_id,
            source_snapshot_id=self.snapshot_id,
            status="incomplete",
            is_active=True,
            assignments=[_module(1, 2), _core(3, 4)],
            payload=_official_payload(role_name),
        )

    def test_unknown_plan_aborts_the_whole_batch(self) -> None:
        plan_id = self._save_plan("早雾", 1003)

        with self.assertRaises(UserDataValidationError):
            self.dao.deactivate_loadout_plans([plan_id, 999999])

        self.assertIn("早雾", self.dao.list_active_loadout_plans_by_role())

    def test_batch_deactivates_every_listed_plan_once(self) -> None:
        first = self._save_plan("早雾", 1003)
        second = self._save_plan("灵可", 1004)

        self.dao.deactivate_loadout_plans([first, second, first])

        self.assertEqual({}, self.dao.list_active_loadout_plans_by_role())

    def test_empty_batch_is_a_no_op(self) -> None:
        self.assertEqual(0, self.dao.deactivate_loadout_plans([]))


if __name__ == "__main__":
    unittest.main()
