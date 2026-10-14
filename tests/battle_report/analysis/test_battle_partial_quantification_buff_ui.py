# 验证 Buff 反事实 UI 不把未知或部分量化冒充完整收益。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QTableWidget

from src.domain.battle_report.counterfactual.quantification import BattleDamageQuantification, BattleQuantificationGap
from src.domain.battle_report.counterfactual.results import BattleDamageCoverage
from src.features.battle_report.analysis.buff_evidence import BattleBuffEvidencePanel
from src.features.battle_report.marginal.results import (
    render_buff_benefit_results,
)
from src.services.battle_report.buffs.counterfactual_plan import (
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
        buff_asset_path=interval.buff_asset_path,
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

    def test_unavailable_does_not_render_zero_gain(self) -> None:
        panel = BattleBuffEvidencePanel()
        self.addCleanup(panel.close)

        panel.render(_analysis(_result(status="unavailable")))

        self.assertEqual("100.0%", panel.table.item(0, 7).text())
        self.assertEqual("—", panel.table.item(0, 8).text())
        self.assertIn("量化状态：unavailable", panel.table.item(0, 9).toolTip())

    def test_partial_value_preserves_unresolved_coverage_and_nullable_gain(self) -> None:
        panel = BattleBuffEvidencePanel()
        self.addCleanup(panel.close)
        fixture = _result(status="partial")
        # 覆盖未知的伤害仍有非零份额，已量化增量不提升为完整收益。
        fixture.damage_coverage = BattleDamageCoverage(1_000.0, 600.0, 250.0)

        panel.render(_analysis(fixture))

        self.assertEqual("至少 60.0%（另 25.0% 未判定）", panel.table.item(0, 7).text())
        self.assertEqual("+11.11%", panel.table.item(0, 8).text())
        self.assertIn("不代表完整 Buff 收益", panel.table.item(0, 9).toolTip())

        fixture.without_quantified_effect_damage = None
        fixture.quantified_damage_gain = None
        fixture.quantified_gain_percent = None
        panel.render(_analysis(fixture))

        self.assertEqual("—", panel.table.item(0, 8).text())
        self.assertIn("部分伤害增量：—", panel.table.item(0, 9).toolTip())

    def test_proven_unchanged_remains_distinct_from_unknown_gain(self) -> None:
        panel = BattleBuffEvidencePanel()
        self.addCleanup(panel.close)

        panel.render(_analysis(_result(status="not_applicable")))

        self.assertEqual("+0.00%", panel.table.item(0, 8).text())

    def test_source_without_covered_hits_keeps_an_explicit_empty_row(self) -> None:
        table = QTableWidget(0, 10)
        self.addCleanup(table.close)
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
        self.assertEqual("—", table.item(0, 6).text())
        self.assertEqual("0.0%", table.item(0, 8).text())


if __name__ == "__main__":
    unittest.main()
