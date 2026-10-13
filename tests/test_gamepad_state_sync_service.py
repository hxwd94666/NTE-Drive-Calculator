# 测试手柄扫描状态同步服务。
from __future__ import annotations

import unittest

from src.integrations.vision.mouse_state_sync import MouseStateIssue, MouseStateSyncResult
from src.services.scanning.state_sync import GamepadStateSyncService


class GamepadStateSyncServiceTests(unittest.TestCase):
    def test_mouse_issues_are_exposed_for_final_scan_summary(self) -> None:
        class Scanner:
            def sync_equipment_states(self, total_drives, changes, *, action_mode):
                self.total_drives = total_drives
                self.changes = changes
                self.action_mode = action_mode
                return MouseStateSyncResult(
                    applied_count=1,
                    issues=(
                        MouseStateIssue(147, "locked", "state_mismatch", "discarded", "normal"),
                        MouseStateIssue(83, "discarded", "identity_mismatch"),
                    ),
                )

        scanner = Scanner()
        summary = GamepadStateSyncService(scanner, total_drives=200).sync(
            [
                {"index": 147, "current_state": "discarded", "target_state": "locked"},
                {"index": 83, "current_state": "normal", "target_state": "discarded"},
                {"index": 1, "current_state": "normal", "target_state": "locked"},
            ],
            {"server_region": "default"},
        )

        self.assertEqual(1, summary["post_action_applied_count"])
        self.assertEqual(2, summary["post_action_issue_count"])
        self.assertEqual([147, 83], [issue["index"] for issue in summary["post_action_issues"]])
        self.assertEqual("identity_mismatch", summary["post_action_issues"][1]["reason"])

    def test_gamepad_integer_result_keeps_existing_summary(self) -> None:
        class Scanner:
            def sync_equipment_states(self, *_args, **_kwargs):
                return 1

        summary = GamepadStateSyncService(Scanner(), total_drives=1).sync(
            [{"index": 1, "current_state": "normal", "target_state": "locked"}], {},
        )
        self.assertEqual(1, summary["post_action_applied_count"])
        self.assertNotIn("post_action_issues", summary)


if __name__ == "__main__":
    unittest.main()
