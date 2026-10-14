# 验证管理角色选择的正式身份、搜索和已有区域配置。
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.features.scanning.post_action_dialog import (
    RoleScopeDialog,
    ScanPostActionDialog,
    _load_role_options,
)
from src.domain.post_actions import default_post_action_config


class PostActionRoleScopeDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_role_search_selects_the_matching_formal_identity(self):
        dialog = RoleScopeDialog(
            None,
            [(1003, "早雾", "", False), (1004, "安魂曲", "", False)],
            [],
        )
        self.addCleanup(dialog.close)
        cards_by_id = {character_id: card for card, character_id, _name in dialog.role_cards}

        dialog.search_edit.setText("zaowu")
        self.app.processEvents()

        self.assertFalse(cards_by_id[1003].isHidden())
        self.assertTrue(cards_by_id[1004].isHidden())
        cards_by_id[1003].click()
        self.assertEqual([1003], dialog.selected_character_ids())


    def test_warehouse_context_hides_region_option_without_resetting_it(self):
        config = default_post_action_config()
        config["server_region"] = "hmt"
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch(
                    "src.features.scanning.post_action_dialog.load_scan_post_action_config",
                    return_value=config,
                ),
                patch(
                    "src.features.scanning.post_action_dialog._load_drive_shape_options",
                    return_value=[],
                ),
                patch(
                    "src.features.scanning.post_action_dialog._load_set_name_options",
                    return_value=[],
                ),
                patch(
                    "src.features.scanning.post_action_dialog._load_role_options",
                    return_value=[],
                ),
            ):
                dialog = ScanPostActionDialog(
                    None,
                    Path(temp_dir),
                    Path(temp_dir),
                    show_server_region_option=False,
                )

        self.assertTrue(dialog.hmt_region_check.isHidden())
        self.assertEqual("hmt", dialog._collect_config()["server_region"])
        dialog.close()

    def test_role_options_identify_account_custom_roles_for_portrait(self):
        class StaticDao:
            def __init__(self, *_args):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def list_role_template_characters(self):
                return [
                    {"character_id": 1042, "name_zh": "黑羽"},
                    {"character_id": 1004, "name_zh": "安魂曲"},
                ]

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
                    [
                        (1004, "安魂曲", "", False),
                        (1042, "黑羽", "", False),
                        (9001, "自建角色", "", True),
                    ],
                    _load_role_options(database_path),
                )

if __name__ == "__main__":
    unittest.main()
