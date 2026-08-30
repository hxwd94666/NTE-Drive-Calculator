# 验证 Buff 反事实 UI 不把未知或部分量化冒充完整收益。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QTableWidget

from src.domain.battle_counterfactual_quantification import (
    BattleDamageQuantification,
    BattleQuantificationGap,
)
from src.domain.battle_buff_counterfactual import BattleDamageCoverage
from src.features.battle_report.buff_evidence_view import BattleBuffEvidencePanel
from src.features.battle_report.marginal_result_table_view import (
    render_buff_benefit_results,
)
from src.features.battle_report.marginal_quantification_view import (
    damage_coverage_text,
)
from src.services.battle_buff_counterfactual_plan_service import (
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
        source_character_id=1001,
        source_character_name="测试角色",
        buff_name="测试 Buff",
        target_scope="team",
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
        beneficiaries=(),
        quantified_unattributed_damage_gain=None,
        unattributed_damage_gain=None,
        damage_coverage=BattleDamageCoverage(
            basis_damage=1_000.0,
            covered_damage=(0.0 if status == "not_applicable" else 1_000.0),
        ),
    )


class BattlePartialQuantificationBuffUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_damage_coverage_labels_unresolved_share_separately(self) -> None:
        self.assertEqual(
            "至少 60.0%（另 25.0% 未判定）",
            damage_coverage_text(BattleDamageCoverage(1_000.0, 600.0, 250.0)),
        )

    def test_buff_audit_keeps_only_decision_columns(self) -> None:
        panel = BattleBuffEvidencePanel()
        headers = tuple(
            panel.table.horizontalHeaderItem(index).text()
            for index in range(panel.table.columnCount())
        )

        self.assertEqual(
            (
                "来源", "作用对象", "Buff", "值", "触发", "时间覆盖",
                "覆盖逐击", "伤害覆盖率", "收益率", "详情",
            ),
            headers,
        )
        self.assertTrue(panel.summary_label.isHidden())

    def test_unavailable_does_not_render_zero_gain(self) -> None:
        panel = BattleBuffEvidencePanel()

        panel.render(_analysis(_result(status="unavailable")))

        self.assertEqual("100.0%", panel.table.item(0, 7).text())
        self.assertEqual("—", panel.table.item(0, 8).text())
        self.assertEqual("查看", panel.table.item(0, 9).text())
        self.assertNotIn("+0.00%", panel.table.item(0, 8).text())
        self.assertIn("量化状态：unavailable", panel.table.item(0, 9).toolTip())

    def test_partial_is_labeled_as_quantified_component(self) -> None:
        panel = BattleBuffEvidencePanel()

        panel.render(_analysis(_result(status="partial")))

        self.assertEqual("100.0%", panel.table.item(0, 7).text())
        self.assertTrue(panel.table.item(0, 8).text().endswith("（部分）"))
        self.assertNotIn("已量化", panel.table.item(0, 8).text())
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

        self.assertEqual("—", panel.table.item(0, 8).text())
        self.assertEqual("查看", panel.table.item(0, 9).text())
        self.assertIn("部分伤害增量：—", panel.table.item(0, 9).toolTip())

    def test_not_applicable_hides_numeric_zero_gain(self) -> None:
        panel = BattleBuffEvidencePanel()

        panel.render(_analysis(_result(status="not_applicable")))

        self.assertEqual("+0.00%", panel.table.item(0, 8).text())
        self.assertEqual("查看", panel.table.item(0, 9).text())

    def test_source_without_covered_hits_keeps_an_explicit_empty_row(self) -> None:
        table = QTableWidget(0, 10)
        unavailable = _result(status="unavailable")
        values = vars(unavailable).copy()
        values.update({
            "affected_hits": 0,
            "quantified_hits": 0,
            "method": "not_covered",
            "damage_coverage": BattleDamageCoverage(basis_damage=1_000.0),
        })

        render_buff_benefit_results(
            table,
            (),
            source_character_id=1001,
            passive_results=(SimpleNamespace(**values),),
        )

        self.assertEqual(1, table.rowCount())
        self.assertEqual("测试角色", table.item(0, 0).text())
        self.assertEqual("测试 Buff", table.item(0, 1).text())
        self.assertEqual("当前范围未覆盖", table.item(0, 2).text())
        self.assertEqual("—", table.item(0, 3).text())
        self.assertNotIn("+0", table.item(0, 6).text())
        self.assertEqual("0.0%", table.item(0, 8).text())


if __name__ == "__main__":
    unittest.main()
