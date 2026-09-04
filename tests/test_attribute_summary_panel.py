# 测试属性汇总面板按调用场景限制可见模式。
from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea, QWidget

from src.ui.attribute_summary_panel import AttributeSummaryPanel, AttributeSummaryRow
from src.ui.equipment_presentation import EquipmentPresentation
from src.features.inventory.equipment_plan_renderer import (
    _saved_official_attribute_panel,
)
from src.features.weighted_allocation.weighted_result_view import _role_option_card
from src.services.allocation_solver import RoleAllocationOption
from src.services.weighted_loadout_comparison_service import WeightedLoadoutComparison


class AttributeSummaryPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_calculation_panel_can_expose_equipment_mode_only(self) -> None:
        panel = AttributeSummaryPanel(
            "角色",
            {"equipment": (AttributeSummaryRow("AtkAdd", "攻击力", 42.0),)},
        )

        labels = [button.text() for button in panel.findChildren(QPushButton)]
        self.assertIn("空幕属性汇总", labels)
        self.assertNotIn("角色属性汇总", labels)

    def test_saved_panel_uses_plain_character_label_and_no_inline_scroll(self) -> None:
        panel = AttributeSummaryPanel(
            "角色",
            {
                "equipment": (),
                "character": tuple(
                    AttributeSummaryRow(str(index), f"属性{index}", float(index))
                    for index in range(8)
                ),
            },
        )

        labels = [button.text() for button in panel.findChildren(QPushButton)]
        self.assertIn("角色属性汇总", labels)
        self.assertFalse(any("（" in label for label in labels))
        self.assertFalse(panel.findChildren(QScrollArea))

    def test_weighted_rows_sort_first_and_color_only_the_attribute_name(self) -> None:
        panel = AttributeSummaryPanel(
            "角色",
            {
                "equipment": (
                    AttributeSummaryRow("low", "低权重", 10.0, weight=0.2),
                    AttributeSummaryRow("high", "高权重", 20.0, weight=0.9),
                    AttributeSummaryRow("mid", "中权重", 30.0, weight=0.5),
                ),
            },
        )

        labels = [
            label for label in panel._content_host.findChildren(QLabel)
            if label.text() in {"低权重", "中权重", "高权重"}
        ]
        self.assertEqual(["高权重", "中权重", "低权重"], [label.text() for label in labels])
        self.assertIn("#f0883e", labels[0].styleSheet())
        values = [
            label for label in panel._content_host.findChildren(QLabel)
            if label.text() in {"+10", "+20", "+30"}
        ]
        self.assertTrue(all("#f0883e" not in label.styleSheet() for label in values))

    def test_saved_panel_keeps_compact_old_new_equipment_comparison(self) -> None:
        old_rows = (
            AttributeSummaryRow("AtkAdd", "攻击力", 100.0, weight=0.2),
            AttributeSummaryRow("CritBase", "暴击率", 10.0, percent=True, weight=0.9),
            AttributeSummaryRow("HPMaxAdd", "生命值", 1000.0, weight=0.5),
        )
        new_rows = (
            AttributeSummaryRow("AtkAdd", "攻击力", 120.0, weight=0.2),
            AttributeSummaryRow("CritBase", "暴击率", 8.0, percent=True, weight=0.9),
            AttributeSummaryRow("HPMaxAdd", "生命值", 1000.0, weight=0.5),
        )
        panel = AttributeSummaryPanel(
            "角色",
            {"equipment": new_rows, "character": new_rows},
            comparison_rows_by_mode={
                "equipment": (old_rows, new_rows),
                "character": (old_rows, new_rows),
            },
        )

        texts = [label.text() for label in panel.findChildren(QLabel)]
        self.assertIn("旧", texts)
        self.assertIn("新", texts)
        self.assertIn("变化", texts)
        self.assertGreaterEqual(panel.minimumWidth(), 560)
        self.assertFalse(panel.findChildren(QScrollArea))
        self.assertIsNotNone(panel.findChild(QWidget, "attributeSummaryComparisonOld"))
        self.assertIsNotNone(panel.findChild(QWidget, "attributeSummaryComparisonNew"))
        self.assertIsNotNone(panel.findChild(QWidget, "attributeSummaryComparisonDelta"))
        old_column = panel.findChild(QWidget, "attributeSummaryComparisonOld")
        old_labels = [
            label for label in old_column.findChildren(QLabel)
            if label.text() in {"攻击力", "暴击率", "生命值"}
        ]
        self.assertEqual(["暴击率", "生命值", "攻击力"], [label.text() for label in old_labels])
        self.assertIn("#f0883e", old_labels[0].styleSheet())
        self.assertIn(
            ("HPMaxAdd", "生命值", 1000.0, 1000.0, False),
            panel._aligned_comparison_rows(old_rows, new_rows),
        )
        panel.set_mode("character")
        character_labels = [
            label.text() for label in panel._content_host.findChildren(QLabel)
        ]
        self.assertEqual(2, character_labels.count("生命值"))
        delta_column = panel.findChild(QWidget, "attributeSummaryComparisonDelta")
        self.assertIsNotNone(delta_column)
        delta_labels = [label.text() for label in delta_column.findChildren(QLabel)]
        self.assertNotIn("生命值", delta_labels)

    def test_saved_official_panel_projects_previous_equipment_summary(self) -> None:
        old = SimpleNamespace(
            key="AtkAdd", label="攻击力", value=100.0, percent=False,
        )
        new = SimpleNamespace(
            key="AtkAdd", label="攻击力", value=120.0, percent=False,
        )
        old_character = SimpleNamespace(
            key="PanelAtk", label="面板攻击力", value=1000.0, percent=False,
        )
        new_character = SimpleNamespace(
            key="PanelAtk", label="面板攻击力", value=1120.0, percent=False,
        )
        panel = _saved_official_attribute_panel(
            "角色",
            {
                "_official_attribute_summaries": {
                    "equipment": (new,), "character": (new_character,),
                },
                "_official_previous_attribute_summaries": {
                    "equipment": (old,), "character": (old_character,),
                },
            },
        )

        self.assertIsNotNone(panel)
        texts = [label.text() for label in panel.findChildren(QLabel)]
        self.assertIn("旧", texts)
        self.assertIn("新", texts)
        self.assertIn("变化", texts)
        buttons = [button.text() for button in panel.findChildren(QPushButton)]
        self.assertIn("角色属性汇总", buttons)
        self.assertFalse(any("（" in text for text in buttons))

        panel.set_mode("character")
        character_texts = [
            label.text() for label in panel._content_host.findChildren(QLabel)
        ]
        self.assertIn("+1000", character_texts)
        self.assertIn("+1120", character_texts)
        self.assertIn("+120", character_texts)

    def test_saved_official_panel_applies_role_weight_sort_and_color(self) -> None:
        low = SimpleNamespace(
            key="HPMaxAdd", label="生命值", value=1000.0, percent=False,
        )
        high = SimpleNamespace(
            key="CritBase", label="暴击率", value=0.1, percent=True,
        )
        panel = _saved_official_attribute_panel(
            "角色",
            {"_official_attribute_summaries": {"equipment": (low, high), "character": ()}},
            weight_for_stat=lambda stat, _mode: {"生命值": 0.2, "暴击率": 0.9}[stat],
        )

        self.assertIsNotNone(panel)
        labels = [
            label for label in panel._content_host.findChildren(QLabel)
            if label.text() in {"生命值", "暴击率"}
        ]
        self.assertEqual(["暴击率", "生命值"], [label.text() for label in labels])
        self.assertIn("#f0883e", labels[0].styleSheet())

    def test_weighted_result_shows_changed_saved_slot_menu(self) -> None:
        comparison = WeightedLoadoutComparison(
            slot_id=9,
            slot_name="输出",
            slot_key="primary",
            old_items=(),
            diff={"changed": True, "added": (), "removed": ()},
        )
        window = SimpleNamespace(
            _weighted_role_names={1003: "角色"},
            _weighted_role_equip_buttons=[],
            _weighted_equipment_actions_available=False,
            _weighted_result_loadout_comparisons={1003: (comparison,)},
        )
        option = RoleAllocationOption(1003, 1, 200.0, (), (), (), ())

        card = _role_option_card(window, option, {}, None, {}, {})

        button = next(
            current for current in card.findChildren(QPushButton)
            if current.text() == "变动"
        )
        self.assertIsNotNone(button.menu())
        self.assertEqual(["输出（主力）"], [action.text() for action in button.menu().actions()])

    def test_calculation_diff_pairs_old_and_new_drive_in_one_change(self) -> None:
        presentation = EquipmentPresentation(
            app_context=SimpleNamespace(
                paths=SimpleNamespace(asset_dir=Path(".")),
                account=SimpleNamespace(user_database_path=Path("user.sqlite3")),
            ),
            dialog_parent=None,
        )
        presentation.update_catalog(
            roles_db={"角色": {"weights": {}, "main_weights": {}}},
            scoring_engine=None,
            shape_areas={"H_2": 2},
        )
        old_item = {
            "uid": "nte-module-1-10", "type": "drive", "shape_id": "L_3_BR",
            "quality": "Gold", "area": 3, "sub_stats": {},
        }
        new_item = {
            "uid": "nte-module-2-20", "type": "drive", "shape_id": "Trap_4_H",
            "quality": "Purple", "area": 4, "sub_stats": {},
        }
        presentation.equipped_state = {
            "角色": {"equipped_tape": None, "equipped_drives": [old_item]}
        }

        dialog = presentation.plan_diff_dialog(
            "角色",
            {
                "changed": True,
                "removed": ({"uid": old_item["uid"], "type": "drive"},),
                "added": (new_item,),
            },
        )

        texts = [label.text() for label in dialog.findChildren(QLabel)]
        self.assertEqual(
            ["变动 1：L_3_BR → Trap_4_H"],
            [text for text in texts if text.startswith("变动 ")],
        )
        self.assertNotIn("  （无需卸下）", texts)
        self.assertNotIn("  （无需换上）", texts)
        self.assertEqual(2, texts.count("0.0"))
        dialog.deleteLater()
        self.app.processEvents()

    def test_diff_score_fallback_covers_drive_and_tape(self) -> None:
        presentation = EquipmentPresentation(
            app_context=SimpleNamespace(
                paths=SimpleNamespace(asset_dir=Path(".")),
                account=SimpleNamespace(user_database_path=Path("user.sqlite3")),
            ),
            dialog_parent=None,
        )
        presentation.update_catalog(
            roles_db={"角色": {"weights": {}, "main_weights": {}}},
            scoring_engine=None,
            shape_areas={"H_2": 2},
        )

        drive = {
            "type": "drive", "shape_id": "H_2", "quality": "Gold",
            "sub_stats": {}, "area": 2,
        }
        tape = {
            "type": "tape", "set_name": "套装", "main_stats": "攻击力%",
            "quality": "Gold", "sub_stats": {}, "area": 15,
        }

        self.assertEqual((0.0, "D"), presentation._diff_item_score_info(drive, "角色"))
        self.assertEqual((0.0, "D"), presentation._diff_item_score_info(tape, "角色"))


if __name__ == "__main__":
    unittest.main()
