# 验证弃置/锁定管理角色弹窗的拼音搜索和紧凑计数文案。
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QToolButton

from src.features.scanning.post_action_dialog import (
    RoleScopeDialog,
    _load_role_options,
)


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

    def test_custom_role_is_rendered_as_a_text_only_card(self):
        dialog = RoleScopeDialog(None, [(900001, "自建角色", "")], [900001])
        card, character_id, role_name = dialog.role_cards[0]
        self.assertEqual(900001, character_id)
        self.assertEqual("自建角色", role_name)
        self.assertEqual(Qt.ToolButtonTextOnly, card.toolButtonStyle())
        self.assertTrue(card.icon().isNull())
        self.assertEqual("已选1名", dialog.count_label.text())
        dialog.close()

    def test_role_options_include_account_custom_roles_without_an_avatar(self):
        class StaticDao:
            def __init__(self, *_args):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def list_role_template_characters(self):
                return [{"character_id": 1004, "name_zh": "安魂曲"}]

        class UserDao:
            def __init__(self, *_args):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def list_custom_characters(self):
                return [{"character_id": 9001, "name_zh": "自建角色"}]

        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "user.sqlite3"
            database_path.touch()
            with (
                patch("src.features.scanning.post_action_dialog.StaticGameDataDao", StaticDao),
                patch("src.features.scanning.post_action_dialog.UserDataDao", UserDao),
                patch("src.features.scanning.post_action_dialog.GameUiAssetCatalog") as catalog,
            ):
                catalog.return_value.character_icon.return_value = None
                self.assertEqual(
                    [(1004, "安魂曲", ""), (9001, "自建角色", "")],
                    _load_role_options(database_path),
                )

if __name__ == "__main__":
    unittest.main()
