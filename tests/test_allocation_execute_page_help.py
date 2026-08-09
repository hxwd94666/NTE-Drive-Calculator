# 验证计算页扫描模式帮助弹窗只接收 QWidget 父对象。
"""Regression coverage for scan-mode help dialog parent ownership."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from src.app.dialogs import show_help
from src.features.allocation.execute_page import _build_scan_mode_card


class AllocationExecutePageHelpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_every_scan_help_button_uses_itself_as_dialog_parent(self) -> None:
        calls: list[tuple[object, str, str]] = []

        class Window:
            def __init__(self) -> None:
                self._ui_preferences = {
                    "full_scan_dual_thread_processing": True,
                    "full_scan_amd_compatibility": False,
                }

            @staticmethod
            def _card(_title: str) -> QWidget:
                card = QWidget()
                QVBoxLayout(card)
                return card

            @staticmethod
            def _on_scan_change(_button_id: int, _checked: bool) -> None:
                pass

            @staticmethod
            def _save_ui_preferences() -> None:
                pass

        host = QWidget()
        layout = QVBoxLayout(host)
        window = Window()

        _build_scan_mode_card(
            window,
            layout,
            {str(index): f"scan-{index}" for index in range(1, 5)},
            {"1": "drone-1", "2": "drone-2"},
            {"full": "offline-full", "incremental": "offline-incremental", "all": "offline-all"},
            lambda parent, title, text: calls.append((parent, title, text)),
        )
        scan_card = layout.itemAt(0).widget()
        self.assertIsNotNone(scan_card)
        buttons = scan_card.findChildren(QPushButton)
        help_buttons = [button for button in buttons if button.objectName() == "btnHelp"]

        self.assertEqual(11, len(help_buttons))
        for button in help_buttons:
            button.click()

        self.assertEqual(11, len(calls))
        self.assertTrue(all(isinstance(parent, QPushButton) for parent, _title, _text in calls))
        self.assertEqual(set(help_buttons), {parent for parent, _title, _text in calls})

    def test_shared_help_dialog_tolerates_a_non_widget_caller(self) -> None:
        with patch("src.app.dialogs.QDialog.exec", return_value=0):
            show_help(object(), "说明", "内容")


if __name__ == "__main__":
    unittest.main()
