# 验证直伤重放的来源项、期望值、抗性和有符号误差。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.domain.battle_report import (
    BattleCharacterBaseline,
    BattleCharacterSourceStat,
    BattleCharacterStat,
    BattleHitBuffProjection,
    BattleProjectedBuffModifier,
    BattleSkillDamageEvidence,
    BattleTargetCondition,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_hit_replay_service import BattleHitReplayService
from src.services.damage_calculation_service import calculate_resistance_multiplier


NTE_TEST_TIER = "core"


class BattleHitReplayFormulaDetailsTests(unittest.TestCase):
    def test_direct_replay_preserves_source_terms_expected_value_and_signed_error(self) -> None:
        baseline = BattleCharacterBaseline(
            character_id=1004,
            character_name="安魂曲",
            source="frozen_v30",
            stats=(
                BattleCharacterStat("AtkBase", "基础攻击力", 1000.0, False),
                BattleCharacterStat("AtkUp", "攻击力提升", 0.25, True),
                BattleCharacterStat("AtkAdd", "固定攻击力", 100.0, False),
                BattleCharacterStat("CritBase", "暴击率", 0.50, True),
                BattleCharacterStat("CritDamageBase", "暴击伤害", 1.0, True),
            ),
            source_stats=(
                BattleCharacterSourceStat(
                    "character", "人物", "AtkBase", "基础攻击力", 800.0, False
                ),
                BattleCharacterSourceStat(
                    "fork", "弧盘", "AtkBase", "基础攻击力", 200.0, False
                ),
                BattleCharacterSourceStat(
                    "equipment", "装备", "AtkUp", "攻击力提升", 0.25, True
                ),
                BattleCharacterSourceStat(
                    "equipment", "装备", "AtkAdd", "固定攻击力", 100.0, False
                ),
                BattleCharacterSourceStat(
                    "character", "人物", "CritBase", "暴击率", 0.50, True
                ),
                BattleCharacterSourceStat(
                    "character", "人物", "CritDamageBase", "暴击伤害", 1.0, True
                ),
            ),
        )
        projection = BattleHitBuffProjection(
            event_id="1:primary",
            modifiers=(
                BattleProjectedBuffModifier(
                    property_id="AtkUp",
                    additive_value=0.16,
                    interval_ids=("buff-1",),
                    buff_names=("测试攻击 Buff",),
                    confidence="高",
                ),
                BattleProjectedBuffModifier(
                    property_id="DamageResistCosmosBase",
                    additive_value=-0.10,
                    interval_ids=("debuff-1",),
                    buff_names=("测试减抗 Debuff",),
                    confidence="中",
                    target_scope="target",
                ),
            ),
            applied_interval_ids=("buff-1", "debuff-1"),
            excluded_interval_ids=(),
            exclusion_reasons=(),
            confidence="高",
        )
        frozen = {row.property_id: row.value for row in baseline.stats}
        values = BattleBuffAttributeProjectionService.apply_additive(
            frozen,
            projection,
        )
        hit = SimpleNamespace(
            event_id="1:primary",
            damage=1000.0,
            classification="direct",
        )
        evidence = BattleSkillDamageEvidence(
            event_id="1:primary",
            damage_id="GE_Test",
            ability_id="GA_Test",
            damage_attribute="cosmos",
            damage_source_category="NORMAL",
            fixed_crit_rate=0.0,
            scaling_property_id="Atk",
            scaling_multiplier=1.0,
            multiplier_coefficient=1.0,
            effective_skill_level=10,
            evidence_basis="测试静态倍率",
        )
        condition = BattleTargetCondition(
            target_name="墨菲斯托",
            enemy_level=90.0,
            scene="outer_realm",
            defense_reduction=0.0,
            vulnerability=0.0,
            resistances=(("cosmos", 0.30),),
            enemy_defense_base=1050.0,
        )

        result = BattleHitReplayService._replay_direct(
            hit=hit,
            evidence=evidence,
            baseline=baseline,
            projection=projection,
            values=values,
            character_level=80.0,
            analysis=SimpleNamespace(target_condition=condition),
            applied_intervals=projection.applied_interval_ids,
            excluded_intervals=(),
        )

        scaling = next(row for row in result.factors if row.factor_id == "scaling")
        self.assertAlmostEqual(1510.0, scaling.value)
        self.assertEqual(
            ("人物", "弧盘", "装备", "装备", "Buff：测试攻击 Buff"),
            tuple(term.source_name for term in scaling.terms),
        )
        resistance = next(
            row for row in result.factors if row.factor_id == "resistance"
        )
        self.assertAlmostEqual(calculate_resistance_multiplier(0.20), resistance.value)
        self.assertIn(
            "Buff：测试减抗 Debuff",
            tuple(term.source_name for term in resistance.terms),
        )
        assert result.non_critical_damage is not None
        assert result.critical_damage is not None
        self.assertEqual(613.0, result.non_critical_damage)
        self.assertEqual(1226.0, result.critical_damage)
        self.assertEqual(
            (result.non_critical_damage + result.critical_damage) / 2.0,
            result.expected_damage,
        )
        self.assertIsNotNone(result.signed_error_percent)
        assert result.selected_damage is not None
        self.assertAlmostEqual(
            (result.selected_damage - 1000.0) / 1000.0 * 100.0,
            result.signed_error_percent,
        )
        self.assertEqual("直伤", result.formula_type)
        self.assertNotIn("dot_final", {row.factor_id for row in result.factors})


if __name__ == "__main__":
    unittest.main()
