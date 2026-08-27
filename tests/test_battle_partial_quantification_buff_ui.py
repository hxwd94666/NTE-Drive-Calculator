# 验证 Buff 反事实 UI 不把未知或部分量化冒充完整收益。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from src.domain.battle_counterfactual_quantification import (
    BattleDamageQuantification,
    BattleQuantificationGap,
)
from src.features.battle_report.buff_evidence_view import BattleBuffEvidencePanel
from src.services.battle_buff_counterfactual_service import (
    battle_buff_counterfactual_key,
)


def _interval():
    return SimpleNamespace(
        interval_id="buff-1",
        source_character_id=1001,
        source_character_name="测试角色",
        source_effect_definition_id="equipment:test:4",
        buff_asset_path="/Game/Buff_Test",
        buff_name="测试 Buff",
        target_scope="team",
        trigger_event_type="进场",
        state_confidence="中",
        value_confidence="中",
        start_us=0,
        end_us=10_000_000,
        modifiers=(),
        inference_basis="静态推算",
    )


def _analysis(result):
    interval = _interval()
    return SimpleNamespace(
        buff_intervals=(interval,),
        buff_counterfactuals=(result,),
        range_start_us=0,
        range_end_us=10_000_000,
        hits=(),
        buff_inference_version="buff-test",
    )


def _result(*, status: str):
    interval = _interval()
    gap = BattleQuantificationGap(
        code="target_resistance_dependency_changed",
        dimension_id="target_resistance",
        dependency_scope="target_sensitive",
        property_ids=("DamageResistChaosBase",),
        explanation="缺少冻结目标抗性画像。",
    )
    if status == "partial":
        quantification = BattleDamageQuantification.from_buckets(
            status="partial",
            partially_quantified_damage=600.0,
            unavailable_damage=400.0,
            quantified_increment=100.0,
            gaps=(gap,),
        )
    elif status == "unavailable":
        quantification = BattleDamageQuantification.from_buckets(
            status="unavailable",
            unavailable_damage=1_000.0,
            quantified_increment=None,
            gaps=(gap,),
        )
    else:
        quantification = BattleDamageQuantification.from_buckets(
            status="not_applicable",
            proven_unchanged_damage=1_000.0,
            quantified_increment=0.0,
        )
    return SimpleNamespace(
        buff_key=battle_buff_counterfactual_key(interval),
        coverage_seconds=10.0,
        affected_hits=1 if status != "not_applicable" else 0,
        quantified_hits=1 if status == "partial" else 0,
        baseline_damage=1_000.0,
        without_quantified_effect_damage=900.0 if status == "partial" else None,
        quantified_damage_gain=100.0 if status == "partial" else None,
        quantified_gain_percent=100.0 / 900.0 * 100.0 if status == "partial" else None,
        without_buff_damage=1_000.0 if status == "not_applicable" else None,
        damage_gain=0.0 if status == "not_applicable" else None,
        gain_percent=0.0 if status == "not_applicable" else None,
        confidence="低",
        method=status,
        explanation="fixture",
        quantification=quantification,
    )


class BattlePartialQuantificationBuffUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_unavailable_does_not_render_zero_gain(self) -> None:
        panel = BattleBuffEvidencePanel()

        panel.render(_analysis(_result(status="unavailable")))

        self.assertEqual("—", panel.table.item(0, 7).text())
        self.assertEqual("未量化", panel.table.item(0, 8).text())
        self.assertEqual("—", panel.table.item(0, 9).text())
        self.assertNotIn("+0.00%", panel.table.item(0, 9).text())
        self.assertIn("未知不记为 0", panel.table.item(0, 9).toolTip())

    def test_partial_is_labeled_as_quantified_component(self) -> None:
        panel = BattleBuffEvidencePanel()

        panel.render(_analysis(_result(status="partial")))

        self.assertEqual("已量化 900.00", panel.table.item(0, 7).text())
        self.assertEqual("已量化 +100.00", panel.table.item(0, 8).text())
        self.assertTrue(panel.table.item(0, 9).text().startswith("已量化 +"))
        self.assertIn("不代表完整 Buff 收益", panel.table.item(0, 9).toolTip())

    def test_partial_nullable_fields_do_not_break_tooltip(self) -> None:
        panel = BattleBuffEvidencePanel()
        fixture = _result(status="partial")
        values = vars(fixture).copy()
        values.update({
            "without_quantified_effect_damage": None,
            "quantified_damage_gain": None,
            "quantified_gain_percent": None,
        })

        panel.render(_analysis(SimpleNamespace(**values)))

        self.assertEqual("—", panel.table.item(0, 7).text())
        self.assertEqual("—", panel.table.item(0, 8).text())
        self.assertEqual("—", panel.table.item(0, 9).text())
        self.assertIn("已量化改动下伤害：—", panel.table.item(0, 9).toolTip())

    def test_not_applicable_hides_numeric_zero_gain(self) -> None:
        panel = BattleBuffEvidencePanel()

        panel.render(_analysis(_result(status="not_applicable")))

        self.assertEqual("不适用", panel.table.item(0, 7).text())
        self.assertEqual("不适用", panel.table.item(0, 8).text())
        self.assertEqual("不适用", panel.table.item(0, 9).text())


if __name__ == "__main__":
    unittest.main()
