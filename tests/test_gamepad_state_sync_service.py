# 测试手柄扫描状态同步服务。
from __future__ import annotations

import unittest

from src.integrations.vision.mouse_state_sync import MouseStateMismatch, MouseStateSyncResult
from src.services.gamepad_state_sync_service import GamepadStateSyncService


class GamepadStateSyncServiceTests(unittest.TestCase):
    def test_mouse_state_mismatches_are_exposed_for_final_scan_summary(self) -> None:
        class Scanner:
            def sync_equipment_states(self, total_drives, changes, *, action_mode):
                self.total_drives = total_drives
                self.changes = changes
                self.action_mode = action_mode
                return MouseStateSyncResult(
                    applied_count=1,
                    state_mismatches=(
                        MouseStateMismatch(147, "discarded", "normal", "locked"),
                    ),
                )

        scanner = Scanner()
        summary = GamepadStateSyncService(scanner, total_drives=200).sync(
            [
                {"index": 147, "current_state": "discarded", "target_state": "locked"},
                {"index": 1, "current_state": "normal", "target_state": "locked"},
            ],
            {"server_region": "default"},
        )

        self.assertEqual(1, summary["post_action_applied_count"])
        self.assertEqual(1, summary["post_action_state_mismatch_count"])
        self.assertEqual((147,), summary["post_action_state_mismatch_indexes"])


if __name__ == "__main__":
    unittest.main()
