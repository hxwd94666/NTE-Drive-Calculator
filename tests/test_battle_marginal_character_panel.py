# 验证战报人物面板完整列出冻结属性并正确合成伤害加权动态总面板。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.domain.battle_report import BattleCharacterBaseline, BattleCharacterStat
from src.features.battle_report.marginal_character_panel import (
    BattleMarginalCharacterPanel,
    character_panel_marginal_units,
)


class BattleMarginalCharacterPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_static_and_weighted_dynamic_rows_cover_full_panel(self) -> None:
        baseline = BattleCharacterBaseline(
            character_id=1036,
            character_name="残虹",
            source="fixture",
            stats=(
                BattleCharacterStat("AtkBase", "基础攻击力", 1000.0, False),
                BattleCharacterStat("AtkAdd", "固定攻击力", 100.0, False),
                BattleCharacterStat("AtkUp", "攻击力提升", 0.20, True),
                BattleCharacterStat("PanelAtk", "总攻击力", 1300.0, False),
                BattleCharacterStat("CritBase", "暴击率", 0.50, True),
                BattleCharacterStat(
                    "DamageUpIncantationBase", "咒属性异能伤", 0.25, True,
                ),
                BattleCharacterStat("ChargeGetEfficiencyBase", "充能效率", 0.30, True),
                BattleCharacterStat("FixtureStat", "测试额外属性", 7.0, False),
            ),
        )
        results = tuple(
            SimpleNamespace(property_id=property_id, weighted_effective_value=value)
            for property_id, value in (
                ("AtkUp", 0.50),
                ("AtkAdd", 140.0),
                ("CritBase", 0.72),
                ("DamageUpIncantationBase", 0.40),
            )
        )
        panel = BattleMarginalCharacterPanel()

        panel.render(
            baseline,
            results,
            current_element_property="DamageUpIncantationBase",
        )

        headers = [
            panel.table.horizontalHeaderItem(index).text()
            for index in range(panel.table.columnCount())
        ]
        columns = {label: index for index, label in enumerate(headers)}
        self.assertEqual("属性", headers[0])
        self.assertEqual("静态面板", panel.table.item(0, 0).text())
        self.assertEqual("动态面板", panel.table.item(1, 0).text())
        self.assertIn("咒属性伤害（本系）", columns)
        self.assertIn("暗属性伤害", columns)
        self.assertIn("测试额外属性", columns)
        self.assertEqual("50.00%", panel.table.item(0, columns["暴击率"]).text())
        self.assertEqual("72.00%", panel.table.item(1, columns["暴击率"]).text())
        self.assertEqual("1,640.00", panel.table.item(1, columns["总攻击力"]).text())
        self.assertEqual("0.00%", panel.table.item(0, columns["暗属性伤害"]).text())
        self.assertEqual("—", panel.table.item(1, columns["充能效率"]).text())
        self.assertEqual(Qt.ScrollBarAsNeeded, panel.table.horizontalScrollBarPolicy())

    def test_panel_projection_units_keep_drive_units_and_add_zero_delta_fields(self) -> None:
        units = character_panel_marginal_units({"CritBase": 0.032})

        self.assertEqual(0.032, units["CritBase"])
        self.assertEqual(0.0, units["DamageUpIncantationBase"])
        self.assertEqual(0.0, units["DamagePenetrateNature"])


if __name__ == "__main__":
    unittest.main()
