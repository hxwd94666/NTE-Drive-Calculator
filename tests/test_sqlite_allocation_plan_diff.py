# 验证计算差异以活动 SQLite 方案为基线，不再读取旧 JSON。
"""Verify calculation diffs use active SQLite plans instead of legacy JSON."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.features.allocation.runner import (
    _plan_changed_uids,
    _persistable_plan_diff,
    _sqlite_allocation_plan_diff,
)
from src.optimizer.contracts import (
    DIFF_ADDED_UIDS,
    DIFF_CHANGED,
    PLAN_ASSIGNED_EXTRA_DRIVES,
    PLAN_ASSIGNED_SET_DRIVES,
    PLAN_ASSIGNED_TAPE,
    PLAN_CHANGED_UIDS,
    PLAN_VALID,
)
from src.storage.sqlite.user_data_dao import UserDataDao


class SqliteAllocationPlanDiffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "user.sqlite3"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_diff_uses_active_sqlite_plan_and_is_payload_safe(self) -> None:
        with UserDataDao(self.database_path, account_id="diff-test") as dao:
            dao.save_loadout_plan(
                name="旧方案",
                character_id=1001,
                source_snapshot_id=None,
                status="saved",
                is_active=True,
                payload={
                    "schema": "allocation-official-snapshot-v1",
                    "source_role_name": "测试角色",
                },
                assignments=[
                    {"uid_serial": 1, "uid_slot": 10, "kind": "module", "target_row": 1, "target_column": 1},
                    {"uid_serial": 2, "uid_slot": 20, "kind": "core", "target_row": None, "target_column": None},
                ],
            )

        final_plan = {
            "测试角色": {
                PLAN_VALID: True,
                PLAN_ASSIGNED_TAPE: {"uid": "nte-core-20-2", "set_name": "套装", "main_stats": "攻击力", "sub_stats": {}, "quality": "Gold"},
                PLAN_ASSIGNED_SET_DRIVES: [{"uid": "nte-module-10-1", "shape_id": "I", "sub_stats": {}, "quality": "Gold", "role_scores": {}}],
                PLAN_ASSIGNED_EXTRA_DRIVES: [{"uid": "nte-module-11-3", "shape_id": "II", "sub_stats": {}, "quality": "Gold", "role_scores": {}}],
            }
        }

        diff = _sqlite_allocation_plan_diff(self.database_path, final_plan)["测试角色"]

        self.assertTrue(diff[DIFF_CHANGED])
        self.assertEqual({"nte-module-11-3"}, diff[DIFF_ADDED_UIDS])
        persisted = _persistable_plan_diff(diff)
        self.assertEqual(["nte-module-11-3"], persisted[DIFF_ADDED_UIDS])
        final_plan["测试角色"][PLAN_CHANGED_UIDS] = {"nte-module-11-3"}
        self.assertEqual({"nte-module-11-3"}, _plan_changed_uids(final_plan["测试角色"]))


if __name__ == "__main__":
    unittest.main()
