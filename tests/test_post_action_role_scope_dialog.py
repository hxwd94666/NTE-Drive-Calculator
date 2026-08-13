# 验证弃置/锁定管理角色弹窗的拼音搜索和紧凑计数文案。
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QToolButton

from src.app.theme import apply_app_theme
from src.features.scanning.post_action_dialog import (
    RoleScopeDialog,
    ScanPostActionDialog,
    _combo as post_action_combo,
)
from src.features.scanning.preserve_rule_editor import _combo as preserve_rule_combo
from src.ui.widgets import NoWheelComboBox


class PostActionRoleScopeDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_role_search_supports_full_pinyin_and_count_is_compact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            avatar_path = os.path.join(temp_dir, "avatar.png")
            avatar = QPixmap(32, 32)
            avatar.fill("#58a6ff")
            self.assertTrue(avatar.save(avatar_path))
            dialog = RoleScopeDialog(
                None,
                [(1003, "早雾", avatar_path), (1004, "安魂曲", avatar_path)],
                [],
            )
            self.assertEqual("已选0名", dialog.count_label.text())
            self.assertTrue(dialog.role_scroll.widgetResizable())
            self.assertTrue(all(isinstance(card[0], QToolButton) for card in dialog.role_cards))
            self.assertFalse(dialog.role_cards[0][0].icon().isNull())
            self.assertTrue(
                all(card.parent() is dialog.role_grid_widget for card, _character_id, _name in dialog.role_cards)
            )
            self.assertTrue(
                all(card not in QApplication.topLevelWidgets() for card, _character_id, _name in dialog.role_cards)
            )

            dialog.search_edit.setText("zaowu")
            self.app.processEvents()

            self.assertFalse(dialog.role_cards[0][0].isHidden())
            self.assertTrue(dialog.role_cards[1][0].isHidden())
            dialog.role_cards[0][0].click()
            self.assertTrue(dialog.role_cards[0][0].isChecked())
            self.assertEqual("已选1名", dialog.count_label.text())
            dialog.close()

    def test_all_management_combo_boxes_ignore_mouse_wheel_selection(self):
        self.assertIsInstance(post_action_combo([("全部", "all")], "all"), NoWheelComboBox)
        self.assertIsInstance(preserve_rule_combo([("全部", "all")], "all"), NoWheelComboBox)

    def test_light_theme_rule_name_uses_the_main_text_color(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            apply_app_theme(self.app, "light")
            try:
                dialog = ScanPostActionDialog(None, Path(temp_dir), Path("config"))
                row = dialog._build_preserve_rule_row(
                    0,
                    {"name": "高分驱动", "item_type": "module", "action": "keep"},
                )
                name = next(
                    label for label in row.findChildren(QLabel)
                    if label.text() == "高分驱动"
                )
                self.assertIn("color:#24292f", name.styleSheet())
                dialog.close()
            finally:
                apply_app_theme(self.app, "dark")

    def test_role_select_button_only_appears_for_selected_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dialog = ScanPostActionDialog(None, Path(temp_dir), Path("config"))
            for widgets in dialog._widgets.values():
                combo = widgets["role_scope"]
                button = widgets["role_scope_button"]
                self.assertEqual("all", combo.currentData())
                self.assertTrue(button.isHidden())

                combo.setCurrentIndex(combo.findData("selected"))
                self.app.processEvents()
                self.assertFalse(button.isHidden())
            dialog.close()


if __name__ == "__main__":
    unittest.main()
