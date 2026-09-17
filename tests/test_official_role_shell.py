# 验证角色页惰性构建期间不创建可闪现的顶层内容窗体。
"""UI lifecycle regression coverage for the official-role tab shell."""

from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.features.official_role import role_shell
from src.features.official_role import role_equipment
from src.features.official_role import role_growth


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

        controller = SimpleNamespace(load_detail=lambda _character_id: detail)
        builders = (
            "_build_base_group", "_build_awakening_group", "_build_skill_group",
            "_build_margin_group", "_build_fork_group", "_build_drive_summary_group",
            "_build_damage_formula_group", "_build_weight_group",
        )
        patches = [patch.object(role_shell, name, side_effect=build_widget) for name in builders]
        with patch.object(role_shell, "_role_controller", return_value=controller):
            for active_patch in patches:
                active_patch.start()
            try:
                role_shell._populate_role_tab(window, scroll, 1001)
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

    def test_equipment_context_selector_is_owned_before_becoming_visible(self) -> None:
        observed_window_state: list[bool] = []
        original = role_equipment.NoWheelComboBox.setVisible

        def observe(combo, visible: bool) -> None:
            if combo.objectName() == "officialRoleEquipmentContextSelector":
                observed_window_state.append(combo.isWindow())
            original(combo, visible)

        detail = {
            "equipment_contexts": {
                "current": {"title": "游戏当前", "items": ()},
            },
            "equipment_plan": {},
        }
        editor = {"equipment_context_key": "current"}
        window = SimpleNamespace(scoring_engine=None)
        with patch.object(
            role_equipment.NoWheelComboBox,
            "setVisible",
            new=observe,
        ), patch.object(
            role_equipment,
            "_calculation_detail",
            return_value=detail,
        ), patch.object(
            role_equipment,
            "calculate_official_role_equipment_gain",
            return_value=None,
        ), patch.object(
            role_equipment,
            "_aggregate_equipment_stats",
            return_value=[],
        ), patch.object(
            role_equipment,
            "_build_equipment_cards_group",
            return_value=QWidget(),
        ):
            group = role_equipment._build_drive_summary_group(
                window,
                detail,
                editor,
            )

        self.assertEqual([False], observed_window_state)
        selector = group.findChild(QWidget, "officialRoleEquipmentContextSelector")
        self.assertIsNotNone(selector)
        self.assertFalse(selector.isWindow())

    def test_fork_damage_margin_is_owned_before_becoming_visible(self) -> None:
        observed_window_state: list[bool] = []
        original = QLabel.setVisible

        def observe(label, visible: bool) -> None:
            if label.text() == "直伤收益: --":
                observed_window_state.append(label.isWindow())
            original(label, visible)

        detail = {
            "profile": {
                "fork_id": None,
                "fork_level": 80,
                "fork_breakthrough_stage": None,
                "fork_refinement_level": 1,
            },
            "forks": (),
        }
        with patch.object(QLabel, "setVisible", new=observe), patch.object(
            role_growth, "fork_breakthrough_choices", return_value=[]
        ), patch.object(
            role_growth, "select_fork_breakthrough", return_value=None
        ), patch.object(
            role_growth, "fork_permanent_stats", return_value={}
        ), patch.object(
            role_growth, "fork_active_panel_stats", return_value={}
        ), patch.object(
            role_growth, "_calculation_detail", return_value={"profile": {}}
        ), patch.object(
            role_growth, "calculate_official_role_margins", return_value=None
        ):
            group = role_growth._build_fork_group(
                SimpleNamespace(), 1001, detail, {}
            )

        self.assertEqual([False], observed_window_state)
        margin = next(
            label for label in group.findChildren(QLabel)
            if label.text() == "直伤收益: --"
        )
        self.assertFalse(margin.isWindow())


if __name__ == "__main__":
    unittest.main()
