# 验证属性单位边际按逐击暴击策略和已有团队倾陷贡献计算。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleHitReplayTerm,
)
from src.services.battle_marginal_calculation_service import (
    BattleMarginalCalculationService,
)


CHARACTER_ID = 1072


def _baseline() -> BattleCharacterBaseline:
    return BattleCharacterBaseline(
        character_id=CHARACTER_ID,
        character_name="灵可",
        source="frozen-test",
        stats=(
            BattleCharacterStat("AtkBase", "基础攻击力", 1000.0, False),
            BattleCharacterStat("AtkUp", "攻击力提升", 0.0, True),
            BattleCharacterStat("AtkAdd", "固定攻击力", 0.0, False),
            BattleCharacterStat("CritBase", "暴击率", 0.5, True),
            BattleCharacterStat("CritDamageBase", "暴击伤害", 1.0, True),
            BattleCharacterStat(
                "DamageUpGeneralBase", "通用伤害增强", 0.0, True
            ),
            BattleCharacterStat(
                "UnbalIntensityBase", "倾陷强度", 100.0, False
            ),
        ),
    )


def _hit(*, classification: str = "direct", damage: float = 1000.0):
    return BattleAnalysisHit(
        event_id="hit:1",
        sequence=1,
        relative_time_us=1_000_000,
        character_id=CHARACTER_ID,
        character_name="灵可",
        skill_name="测试技能",
        damage_name="测试伤害",
        damage_component="skill",
        attack_type="normal",
        damage_attribute="nature",
        target_id="target:1",
        target_name="目标",
        damage=damage,
        direction="outgoing",
        is_follow_up=False,
        classification=classification,
    )


def _analysis(hit, replay):
    return SimpleNamespace(
        baselines=(_baseline(),),
        hits=(hit,),
        hit_replays=(replay,),
        buff_intervals=(),
        roles=(
            SimpleNamespace(
                character_id=CHARACTER_ID,
                max_hp_reduction_damage=0.0,
            ),
        ),
        effective_damage=hit.damage,
        build_counterfactual=None,
        target_condition=None,
    )


def _critical_replay(hit, policy: str, rate: float | None):
    return BattleHitReplayResult(
        event_id=hit.event_id,
        observed_damage=hit.damage,
        non_critical_damage=hit.damage,
        critical_damage=hit.damage * 2.0,
        selected_damage=hit.damage,
        selected_error_percent=0.0,
        critical_state=("not_applicable" if policy == "disabled" else "ambiguous"),
        confidence="高",
        factors=(),
        critical_rate=rate,
        expected_damage=None if policy == "unknown" else hit.damage,
        critical_policy=policy,
    )


class BattleMarginalCalculationServiceTests(unittest.TestCase):
    def test_unit_margin_anchors_on_current_candidate_projection(self) -> None:
        hit = _hit()
        analysis = _analysis(hit, _critical_replay(hit, "character", 0.5))
        analysis.build_counterfactual = SimpleNamespace(
            hits=(SimpleNamespace(event_id=hit.event_id, predicted_damage=1500.0),),
            roles=(
                SimpleNamespace(
                    character_id=CHARACTER_ID,
                    predicted_damage=1500.0,
                ),
            ),
            predicted_damage=2000.0,
        )

        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=CHARACTER_ID,
            edited_values={},
            units={"AtkUp": 0.0125},
        )[0]

        self.assertAlmostEqual(1500.0, result.baseline_damage)
        self.assertAlmostEqual(1518.75, result.predicted_damage)
        self.assertAlmostEqual(1.25, result.role_gain_percent)
        self.assertAlmostEqual(0.9375, result.team_dps_gain_percent)
        self.assertAlmostEqual(100.0, result.coverage_percent)
        self.assertAlmostEqual(75.0, result.damage_share_percent)

    def test_crit_units_follow_each_hit_critical_policy(self) -> None:
        hit = _hit()
        units = {"CritBase": 0.01, "CritDamageBase": 0.02}

        character_rows = BattleMarginalCalculationService.calculate(
            analysis=_analysis(hit, _critical_replay(hit, "character", 0.5)),
            character_id=CHARACTER_ID,
            edited_values={},
            units=units,
        )
        self.assertGreater(
            next(row for row in character_rows if row.property_id == "CritBase")
            .role_gain_percent,
            0.0,
        )

        fixed_rows = BattleMarginalCalculationService.calculate(
            analysis=_analysis(hit, _critical_replay(hit, "fixed", 0.5)),
            character_id=CHARACTER_ID,
            edited_values={},
            units=units,
        )
        fixed_rate = next(row for row in fixed_rows if row.property_id == "CritBase")
        fixed_damage = next(
            row for row in fixed_rows if row.property_id == "CritDamageBase"
        )
        self.assertEqual(fixed_rate.baseline_damage, fixed_rate.predicted_damage)
        self.assertEqual(0.0, fixed_rate.coverage_percent)
        self.assertGreater(fixed_damage.role_gain_percent, 0.0)

        for policy, rate in (("disabled", 0.0), ("unknown", None)):
            rows = BattleMarginalCalculationService.calculate(
                analysis=_analysis(hit, _critical_replay(hit, policy, rate)),
                character_id=CHARACTER_ID,
                edited_values={},
                units=units,
            )
            self.assertTrue(all(row.role_gain_percent == 0.0 for row in rows))
            self.assertTrue(all(row.coverage_percent == 0.0 for row in rows))

    def test_topple_unit_reuses_source_character_contribution(self) -> None:
        hit = _hit(classification="topple")
        strength_term = BattleHitReplayTerm(
            term_id="character:1072:UnbalIntensityBase",
            property_id="UnbalIntensityBase",
            label="倾陷强度",
            value=100.0,
            source_group="resolved",
            source_name="角色面板",
            is_percent=False,
            evidence_basis="冻结角色面板",
        )
        replay = BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=1000.0,
            non_critical_damage=2000.0,
            critical_damage=None,
            selected_damage=2000.0,
            selected_error_percent=100.0,
            critical_state="not_applicable",
            confidence="低",
            factors=(
                BattleHitReplayFactor(
                    factor_id="topple_character:1072",
                    label="灵可倾陷贡献",
                    value=4000.0 / 3.0,
                    evidence_basis="逐角色倾陷公式",
                    terms=(strength_term,),
                ),
                BattleHitReplayFactor(
                    factor_id="topple_character:1001",
                    label="队友倾陷贡献",
                    value=2000.0 / 3.0,
                    evidence_basis="逐角色倾陷公式",
                ),
            ),
            critical_rate=0.0,
            expected_damage=2000.0,
            critical_policy="disabled",
        )
        analysis = _analysis(hit, replay)

        units = BattleMarginalCalculationService.default_units(
            analysis.baselines[0]
        )
        self.assertEqual(6.0, units["UnbalIntensityBase"])
        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=CHARACTER_ID,
            edited_values={},
            units={"UnbalIntensityBase": 6.0},
        )[0]

        self.assertEqual(1000.0, result.supported_damage)
        self.assertAlmostEqual(100.0, result.coverage_percent)
        self.assertAlmostEqual(1010.0, result.predicted_damage)
        self.assertAlmostEqual(1.0, result.role_gain_percent)
        self.assertAlmostEqual(1.0, result.team_dps_gain_percent)


if __name__ == "__main__":
    unittest.main()
