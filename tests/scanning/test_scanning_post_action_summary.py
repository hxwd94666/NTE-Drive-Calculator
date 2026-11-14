# 测试扫描后管理结果汇总。
from __future__ import annotations

import unittest
from unittest.mock import patch

from src.features.scanning.post_actions.post_action_summary import (
    append_post_action_issue_summary,
    append_scan_post_action_summary,
    post_action_issue_details,
    show_scan_completion,
)


class ScanPostActionSummaryTests(unittest.TestCase):
    def test_issues_are_summarized_without_a_long_index_list(self) -> None:
        summary = append_post_action_issue_summary(
            "扫描完成",
            {
                "post_action_applied_count": 98,
                "post_action_issue_count": 2,
            },
        )

        self.assertEqual(
            "扫描完成\n状态管理已结束：成功 98 件，跳过 2 件。"
            "跳过的装备未执行状态修改，请查看详情。",
            summary,
        )

    def test_no_mismatch_keeps_the_existing_notice(self) -> None:
        self.assertEqual("扫描完成", append_post_action_issue_summary("扫描完成", {}))
        self.assertEqual("扫描完成", append_scan_post_action_summary("扫描完成", {}))

    def test_details_show_both_issue_types_and_planned_action(self) -> None:
        stats = {"post_action_issues": (
            {"index": 147, "reason": "identity_mismatch", "target_state": "locked"},
            {"index": 83, "reason": "state_mismatch", "target_state": "normal"},
        )}
        self.assertEqual(
            "第 147 件：详情与扫描截图不一致，已跳过原计划的锁定操作。\n"
            "第 83 件：当前状态与计划不一致，已跳过原计划的恢复普通状态操作。",
            post_action_issue_details(stats),
        )

    def test_planned_counts_are_not_presented_as_completed_when_items_are_skipped(self) -> None:
        stats = {
            "post_actions_enabled": True, "post_action_applied_count": 0,
            "post_action_issue_count": 2, "lock_set_count": 2,
        }
        summary = append_scan_post_action_summary("扫描完成", stats)
        self.assertIn("计划弃置", summary)
        self.assertIn("成功 0 件，跳过 2 件", summary)

    def test_one_warning_has_expandable_details_after_completion(self) -> None:
        module = "src.features.scanning.post_actions.post_action_summary"
        stats = {
            "post_action_issue_count": 1,
            "post_action_issues": ({"index": 147, "reason": "identity_mismatch", "target_state": "locked"},),
        }
        with patch(f"{module}.QMessageBox") as message, patch(f"{module}.QApplication") as app, \
                patch(f"{module}.fit_dialog_to_available_screen") as fit:
            show_scan_completion(None, "扫描完成", "状态管理已结束", stats)
        message.information.assert_not_called()
        message.return_value.exec.assert_called_once()
        message.return_value.setDetailedText.assert_called_once_with(post_action_issue_details(stats))
        fit.assert_called_once_with(message.return_value)
        app.beep.assert_called_once()

    def test_no_issues_keeps_existing_information_dialog(self) -> None:
        module = "src.features.scanning.post_actions.post_action_summary"
        with patch(f"{module}.QMessageBox") as message, patch(f"{module}.QApplication") as app:
            show_scan_completion(None, "扫描完成", "原完成说明", {})
        message.information.assert_called_once_with(None, "扫描完成", "原完成说明")
        message.assert_not_called()
        app.beep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
