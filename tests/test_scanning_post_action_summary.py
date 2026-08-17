# 测试扫描后管理结果汇总。
from __future__ import annotations

import unittest

from src.features.scanning.post_action_summary import append_state_mismatch_summary


class ScanPostActionSummaryTests(unittest.TestCase):
    def test_state_mismatches_are_added_to_the_completed_scan_notice(self) -> None:
        summary = append_state_mismatch_summary(
            "扫描完成",
            {
                "post_action_state_mismatch_count": 2,
                "post_action_state_mismatch_indexes": (147, 83),
            },
        )

        self.assertEqual(
            "扫描完成\n状态与扫描计划不一致，已跳过 2 件且未执行操作：第 147 件、第 83 件。",
            summary,
        )

    def test_no_mismatch_keeps_the_existing_notice(self) -> None:
        self.assertEqual("扫描完成", append_state_mismatch_summary("扫描完成", {}))


if __name__ == "__main__":
    unittest.main()
