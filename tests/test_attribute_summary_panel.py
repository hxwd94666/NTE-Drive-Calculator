# 验证保存方案的历史面板值及百分比单位在展示时保持原义。
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from src.features.inventory.equipment.plan_renderer import (
    _saved_official_attribute_panel,
)


class AttributeSummaryPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

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
        self.addCleanup(panel.close)
        panel.set_mode("character")
        character_texts = [
            label.text() for label in panel._content_host.findChildren(QLabel)
        ]
        self.assertIn("+1000", character_texts)
        self.assertIn("+1120", character_texts)
        self.assertIn("+120", character_texts)

    def test_official_attack_percent_keeps_percent_label(self) -> None:
        attack_percent = SimpleNamespace(
            key="AtkUp",
            label="攻击力",
            value=0.3575,
            percent=True,
            weight_property_ids=("AtkUp",),
        )
        panel = _saved_official_attribute_panel(
            "角色",
            {
                "_official_attribute_summaries": {
                    "equipment": (attack_percent,),
                    "character": (),
                }
            },
            weight_for_stat=lambda stat, _mode: {"攻击力%": 0.9}.get(stat, 0.4),
        )
        self.addCleanup(panel.close)

        next(
            current for current in panel._content_host.findChildren(QLabel)
            if current.text() == "攻击力%"
        )

if __name__ == "__main__":
    unittest.main()
