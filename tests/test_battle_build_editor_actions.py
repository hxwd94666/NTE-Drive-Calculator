# 验证战报角色编辑弹窗的单向装备同步和入口位置。
from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from src.features.battle_report.analysis_view import BattleLongAnalysisView
from src.features.battle_report.build_snapshot_editor import (
    BattleBuildSnapshotEditorDialog,
)


class BattleBuildEditorActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_editor_exposes_five_one_way_sync_actions(self) -> None:
        class FakeRoleEditor(QWidget):
            def __init__(self, _detail, parent=None, **_kwargs) -> None:
                super().__init__(parent)

        with patch(
            "src.features.battle_report.build_snapshot_editor."
            "OfficialRoleProfileEditor",
            FakeRoleEditor,
        ):
            dialog = BattleBuildSnapshotEditorDialog(
                {
                    "has_edit": True,
                    "details": [
                        {"character": {"character_id": 1004, "name_zh": "安魂曲"}}
                    ],
                }
            )

        self.assertEqual(
            {
                "取消",
                "从角色页面同步（不含空幕驱动）",
                "从角色页面同步（含空幕驱动）",
                "保存修改副本",
                "保存并同步到角色页（不含空幕驱动）",
            },
            {button.text() for button in dialog.findChildren(QPushButton)},
        )

    def test_build_editor_is_immediately_left_of_current_scope(self) -> None:
        view = BattleLongAnalysisView()

        edit_index = view.context_row.indexOf(view.build_edit_control)
        current_label_index = view.context_row.indexOf(view.current_scope_title)

        self.assertEqual(current_label_index - 1, edit_index)


if __name__ == "__main__":
    unittest.main()
