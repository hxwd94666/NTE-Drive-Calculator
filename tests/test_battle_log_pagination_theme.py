# 验证逐击日志单页禁用翻页，以及各主题下禁用按钮与可用按钮的视觉区别。
import unittest
from dataclasses import replace
from types import SimpleNamespace

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from src.app.theme import apply_app_theme
from src.features.battle_report.analysis_view import BattleLongAnalysisView
from tests.test_battle_hit_buff_detail import _hit


class BattleLogPaginationThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_single_page_disables_both_buttons_and_multiple_pages_enable_next(self):
        for theme in ("dark", "black", "light"):
            with self.subTest(theme=theme):
                apply_app_theme(self.app, theme)
                view = BattleLongAnalysisView()
                view._log_page_size = 2
                view._analysis = SimpleNamespace(hits=(_hit(),))
                view._render_log()
                view.log_dialog.show()
                self.app.processEvents()
                self.assertFalse(view.prev_button.isEnabled())
                self.assertFalse(view.next_button.isEnabled())
                disabled = view.next_button.palette().color(
                    QPalette.Disabled, QPalette.ButtonText,
                )
                view._analysis = SimpleNamespace(hits=tuple(
                    replace(_hit(), event_id=str(i), sequence=i) for i in range(3)
                ))
                view._render_log()
                self.app.processEvents()
                self.assertTrue(view.next_button.isEnabled())
                self.assertNotEqual(disabled, view.next_button.palette().color(
                    QPalette.Active, QPalette.ButtonText,
                ))
                view.next_button.click()
                self.assertEqual(1, view._log_page)
                self.assertFalse(view.next_button.isEnabled())
                self.assertTrue(view.prev_button.isEnabled())
                view.log_dialog.close()
                view.close()
