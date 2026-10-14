# 验证战报角色编辑弹窗的单向装备同步和入口位置。
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QLabel, QWidget

from src.features.battle_report.editing.dialog import (
    BattleBuildSnapshotEditorDialog,
)
from src.ui.widgets import NoWheelComboBox


class BattleBuildEditorActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_replacement_uses_frozen_static_input_and_rejects_stale_editor(self) -> None:
        handlers = []

        class FakeRoleEditor(QWidget):
            def __init__(self, _detail, parent=None, **kwargs) -> None:
                super().__init__(parent)
                handlers.append(kwargs["equipment_replacement_handler"])

        editor_data = {
            "has_edit": True,
            "details": [
                {
                    "character": {"character_id": role_id, "name_zh": f"角色{role_id}"},
                    "equipment_contexts": {"original": {"items": []}},
                    "static_database_path": Path("frozen-static.sqlite3"),
                    "static_schema_version": 42,
                }
                for role_id in (1004, 1005)
            ],
        }
        with patch(
            "src.features.battle_report.editing.dialog.OfficialRoleProfileEditor",
            FakeRoleEditor,
        ):
            dialog = BattleBuildSnapshotEditorDialog(editor_data)
        self.addCleanup(dialog.deleteLater)
        target = {"kind": "drive", "slot": 1}

        def choose(parent, detail, received_target, *, static_database_path,
                   static_schema_version, is_current, **_kwargs):
            self.assertIs(parent, dialog)
            self.assertIsNot(detail, editor_data["details"][0])
            self.assertEqual(received_target, target)
            self.assertEqual(static_database_path, Path("frozen-static.sqlite3"))
            self.assertEqual(static_schema_version, 42)
            self.assertTrue(is_current())
            # 模态候选准备仍泵 Qt 事件：换页签或替换草稿后，旧选择不再属于当前编辑器。
            dialog.tabs.setCurrentIndex(1)
            self.assertFalse(is_current())
            dialog.tabs.setCurrentIndex(0)
            self.assertTrue(is_current())
            dialog._details[0] = dict(detail)
            self.assertFalse(is_current())
            return False

        with patch(
            "src.features.battle_report.editing.dialog.show_battle_equipment_replacement",
            autospec=True,
            side_effect=choose,
        ) as picker:
            self.assertFalse(handlers[0](target, "original"))
        picker.assert_called_once()
        self.assertEqual(editor_data["details"][0]["equipment_contexts"]["original"],
                         {"items": []})

    def test_accepted_replacement_preserves_cultivation_and_selected_context(self) -> None:
        target = {"kind": "core", "item_id": "fixture", "uid_slot": 1, "uid_serial": 1}
        replacement = {**target, "uid_serial": 2}
        detail = {
            "character": {"character_id": 1004, "name_zh": "合成角色"},
            "profile": {"character_id": 1004, "character_level": 70,
                        "breakthrough_stage": 5, "awakening_level": 0},
            "growth_rows": [{"level": 70, "breakthrough_stage": 5},
                            {"level": 80, "breakthrough_stage": 6}],
            "forks": [], "skills": [], "attributes": {},
            "selected_equipment_context_key": "original",
            "static_database_path": Path("frozen-static.sqlite3"),
            "static_schema_version": 42,
            "equipment_contexts": {
                "original": {"title": "原始配装", "items": []},
                "chosen": {"title": "选择的配装", "items": [target]},
            },
        }
        cards = []

        def card(_window, _detail, item, *, replacement_callback, **_kwargs):
            cards.append((item["uid_serial"], replacement_callback))
            return QLabel(str(item["uid_serial"]))

        def choose(_parent, _detail, _target, *, on_replaced, is_current, **_kwargs):
            self.assertTrue(is_current())
            on_replaced(replacement)
            return True

        # 保留真实编辑器、选择器和装备刷新链；仅隔离装饰卡片与候选对话框。
        with (
            patch("src.features.official_role.role_equipment._equipment_item_card",
                  side_effect=card),
            patch("src.features.battle_report.editing.dialog.show_battle_equipment_replacement",
                  autospec=True, side_effect=choose),
        ):
            dialog = BattleBuildSnapshotEditorDialog({"details": [detail], "has_edit": True})
            self.addCleanup(dialog.deleteLater)
            editor = dialog._editors[0]
            editor._editor["growth"].setValue(80)
            selector = editor.findChild(NoWheelComboBox, "officialRoleEquipmentContextSelector")
            selector.setCurrentIndex(selector.findData("chosen"))
            before = dialog.profiles()[0]
            self.assertEqual((80, "chosen"),
                             (before["character_level"], before["equipment_context_key"]))
            self.assertTrue(cards[-1][1]())
            after = dialog.profiles()[0]

        before_equipment = before.pop("equipment_override")
        after_equipment = after.pop("equipment_override")
        self.assertEqual(before, after)
        self.assertEqual([1], [item["uid_serial"] for item in before_equipment])
        self.assertEqual([2], [item["uid_serial"] for item in after_equipment])
        self.assertEqual(2, cards[-1][0])
        self.assertEqual(70, detail["profile"]["character_level"])
        self.assertEqual([target], detail["equipment_contexts"]["chosen"]["items"])

    def test_imported_editor_hides_equipment_sync_and_submits_only_cultivation(self) -> None:
        created_options = []

        class FakeRoleEditor(QWidget):
            def __init__(self, _detail, parent=None, **kwargs) -> None:
                super().__init__(parent)
                created_options.append(kwargs)

            def profile(self):
                return {
                    "character_id": 1004,
                    "character_level": 80,
                    "skill_levels": {"melee": 10},
                }

            def selected_equipment_context(self):
                raise AssertionError("导入战报保存不应读取装备上下文")

        with patch(
            "src.features.battle_report.editing.dialog."
            "OfficialRoleProfileEditor",
            FakeRoleEditor,
        ):
            dialog = BattleBuildSnapshotEditorDialog(
                {
                    "has_edit": True,
                    "equipment_editable": False,
                    "details": [
                        {"character": {"character_id": 1004, "name_zh": "安魂曲"}}
                    ],
                }
            )

        self.assertTrue(dialog.import_all_button.isHidden())
        self.assertFalse(created_options[0]["allow_equipment_replacement"])
        self.assertFalse(created_options[0]["show_equipment_context_selector"])
        self.assertNotIn("equipment_override", dialog.profiles()[0])


if __name__ == "__main__":
    unittest.main()
