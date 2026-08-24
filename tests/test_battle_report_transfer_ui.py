# 验证战报包弹窗的多选与账号昵称行为。

from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from src.domain.battle_report_transfer import BattleReportTransferEntry
from src.features.battle_report.transfer_dialog import BattleReportTransferDialog


class BattleReportTransferDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_multi_select_all_clear_and_account_name_signal(self) -> None:
        dialog = BattleReportTransferDialog()
        saved_names = []
        dialog.account_name_save_requested.connect(saved_names.append)
        entries = tuple(
            BattleReportTransferEntry(
                battle_record_id=record_id,
                captured_at_utc="2026-08-24T01:00:00+00:00",
                gameplay_label="轨外之境 · 第 8 层",
                scope_label="上半场",
                completeness_label="逐击完整 · 2 条",
                cursor_label="尾页已排空 · cursor 3",
                retention_label="自动保存",
                total_hits=2,
            )
            for record_id in (11, 12)
        )
        try:
            dialog.set_account_name("原昵称")
            dialog.set_entries(entries)
            self.assertEqual((), dialog.selected_report_ids())

            dialog.select_all_button.click()
            self.assertEqual((11, 12), dialog.selected_report_ids())
            dialog.clear_selection_button.click()
            self.assertEqual((), dialog.selected_report_ids())

            dialog.account_name_edit.setText("新昵称")
            dialog.account_name_edit.setModified(True)
            self.assertTrue(dialog.has_unsaved_account_name())
            dialog.save_name_button.click()
            self.assertEqual(["新昵称"], saved_names)
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
