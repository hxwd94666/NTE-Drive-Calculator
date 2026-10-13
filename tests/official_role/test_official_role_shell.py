# 验证角色页惰性构建期间不创建可闪现的顶层内容窗体。
"""UI lifecycle regression coverage for the official-role tab shell."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

from src.features.official_role import role_shell


class OfficialRoleShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_lazy_role_content_is_parented_and_hidden_while_building(self) -> None:
        detail = {
            "property_weights": {},
            "main_property_weights": {},
            "equipment_contexts": {"saved": {"available": False}},
        }
        window = SimpleNamespace(_official_role_editors={})
        scroll = QScrollArea()
        observations: list[tuple[bool, bool]] = []

        def build_widget(*_args: object) -> QWidget:
            candidates = scroll.viewport().findChildren(
                QWidget, options=Qt.FindDirectChildrenOnly
            )
            content = next(
                (candidate for candidate in candidates if candidate.layout() is not None),
                None,
            )
            observations.append((content is not None, bool(content and content.isHidden())))
            return QWidget()

        builders = (
            "_build_base_group", "_build_awakening_group", "_build_skill_group",
            "_build_margin_group", "_build_fork_group", "_build_drive_summary_group",
            "_build_damage_formula_group", "_build_weight_group",
        )
        patches = [patch.object(role_shell, name, side_effect=build_widget) for name in builders]
        with patch.object(role_shell, "_role_controller"):
            for active_patch in patches:
                active_patch.start()
            try:
                role_shell._render_role_tab(window, scroll, 1001, detail)
                deadline = time.monotonic() + 2
                while not scroll.property("loaded") and time.monotonic() < deadline:
                    self.app.processEvents()
            finally:
                for active_patch in reversed(patches):
                    active_patch.stop()

        self.assertTrue(observations)
        self.assertTrue(all(parented and hidden for parented, hidden in observations))
        self.assertTrue(scroll.property("loaded"))
        self.assertIsNotNone(scroll.widget())
        self.assertFalse(scroll.widget().isWindow())
        self.assertTrue(scroll.updatesEnabled())

    def test_layout_cleanup_never_detaches_widgets_as_toplevel_windows(self) -> None:
        owner = QWidget()
        layout = QVBoxLayout(owner)
        child = QWidget(owner)
        layout.addWidget(child)

        role_shell._clear_layout(layout)

        self.assertIs(child.parent(), owner)
        self.assertFalse(child.isWindow())
        self.assertTrue(child.isHidden())




if __name__ == "__main__":
    unittest.main()
