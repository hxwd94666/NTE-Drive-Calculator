# 验证属性单位边际按逐击暴击策略和已有团队倾陷贡献计算。
from __future__ import annotations

import unittest
from dataclasses import dataclass
from types import SimpleNamespace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleHitReplayTerm,
    BattleTargetCondition,
)
from src.domain.battle_target import BattleTargetInstanceResolution
from src.services.battle_marginal_calculation_service import (
    BattleMarginalCalculationService,
)


CHARACTER_ID = 1072


@dataclass
class _AnalysisFixture:
    baselines: tuple
    hits: tuple
    hit_replays: tuple
    buff_intervals: tuple
    roles: tuple
    effective_damage: float
    build_counterfactual: object | None
    target_condition: BattleTargetCondition | None
    target_conditions_by_half: tuple
    target_instance_resolutions: tuple
    target_instance_mapping_required: bool


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


def _hit(
    *,
    event_id: str = "hit:1",
    classification: str = "direct",
    damage: float = 1000.0,
    scope_half: str = "",
    target_id: str = "target:1",
):
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=1,
        relative_time_us=1_000_000,
        character_id=CHARACTER_ID,
        character_name="灵可",
        skill_name="测试技能",
        damage_name="测试伤害",
        damage_component="skill",
        attack_type="normal",
        damage_attribute="nature",
        target_id=target_id,
        target_name="目标",
        damage=damage,
        direction="outgoing",
        is_follow_up=False,
        classification=classification,
        scope_half=scope_half,
    )


def _analysis(
    hit,
    replay,
    *,
    target_condition=None,
    target_instance_resolutions=(),
    target_instance_mapping_required=False,
):
    return _AnalysisFixture(
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
        target_condition=target_condition,
        target_conditions_by_half=(),
        target_instance_resolutions=target_instance_resolutions,
        target_instance_mapping_required=target_instance_mapping_required,
    )


def _target_condition(*, defense: float, resistance: float) -> BattleTargetCondition:
    return BattleTargetCondition(
        target_name="测试目标",
        enemy_level=90.0,
        scene="outer_realm",
        defense_reduction=0.0,
        vulnerability=0.0,
        resistances=(("nature", resistance),),
        enemy_defense_base=defense,
    )


def _target_resolution(
    *,
    scope_half: str,
    target_id: str,
    condition: BattleTargetCondition | None,
) -> BattleTargetInstanceResolution:
    return BattleTargetInstanceResolution(
        scope_half=scope_half,
        captured_target_id=target_id,
        resolved_monster_id="",
        default_monster_id="",
        possible_monster_ids=(),
        resolution_mode="fixture",
        initial_max_hp=1000.0,
        target_condition=condition,
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
    def test_unknown_target_attack_margin_anchors_on_candidate_projection(self) -> None:
        hit = _hit()
        analysis = _analysis(hit, _critical_replay(hit, "character", 0.5))
        analysis.build_counterfactual = SimpleNamespace(
            hits=(
                SimpleNamespace(
                    event_id=hit.event_id,
                    predicted_damage=1500.0,
                ),
            ),
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

    def test_legacy_single_target_condition_remains_supported(self) -> None:
        hit = _hit()
        condition = _target_condition(defense=1000.0, resistance=0.2)
        analysis = _analysis(
            hit,
            _critical_replay(hit, "character", 0.5),
            target_condition=condition,
        )

        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=CHARACTER_ID,
            edited_values={},
            units={"DefIgnore": 0.01},
        )[0]

        self.assertGreater(result.role_gain_percent, 0.0)
        self.assertEqual(100.0, result.coverage_percent)

    def test_same_target_id_in_two_halves_uses_each_frozen_profile(self) -> None:
        upper = _hit(event_id="hit:upper", scope_half="upper", target_id="7")
        lower = _hit(event_id="hit:lower", scope_half="lower", target_id="7")
        upper_condition = _target_condition(defense=600.0, resistance=0.1)
        lower_condition = _target_condition(defense=1600.0, resistance=0.4)
        upper_replay = _critical_replay(upper, "character", 0.5)
        lower_replay = _critical_replay(lower, "character", 0.5)
        combined = _analysis(
            upper,
            upper_replay,
            target_condition=upper_condition,
            target_instance_resolutions=(
                _target_resolution(
                    scope_half="upper",
                    target_id="7",
                    condition=upper_condition,
                ),
                _target_resolution(
                    scope_half="lower",
                    target_id="7",
                    condition=lower_condition,
                ),
            ),
            target_instance_mapping_required=True,
        )
        combined.hits = (upper, lower)
        combined.hit_replays = (upper_replay, lower_replay)
        combined.effective_damage = upper.damage + lower.damage

        combined_result = BattleMarginalCalculationService.calculate(
            analysis=combined,
            character_id=CHARACTER_ID,
            edited_values={},
            units={"DefIgnore": 0.01},
        )[0]
        upper_result = BattleMarginalCalculationService.calculate(
            analysis=_analysis(
                upper,
                upper_replay,
                target_condition=upper_condition,
            ),
            character_id=CHARACTER_ID,
            edited_values={},
            units={"DefIgnore": 0.01},
        )[0]
        lower_result = BattleMarginalCalculationService.calculate(
            analysis=_analysis(
                lower,
                lower_replay,
                target_condition=lower_condition,
            ),
            character_id=CHARACTER_ID,
            edited_values={},
            units={"DefIgnore": 0.01},
        )[0]

        expected_increment = (
            upper_result.predicted_damage
            - upper_result.baseline_damage
            + lower_result.predicted_damage
            - lower_result.baseline_damage
        )
        self.assertAlmostEqual(
            expected_increment,
            combined_result.predicted_damage - combined_result.baseline_damage,
        )
        self.assertEqual(100.0, combined_result.coverage_percent)

    def test_missing_instance_profile_does_not_fall_back_to_primary(self) -> None:
        hit = _hit(scope_half="lower", target_id="7")
        primary = _target_condition(defense=1000.0, resistance=0.2)
        analysis = _analysis(
            hit,
            _critical_replay(hit, "character", 0.5),
            target_condition=primary,
            target_instance_resolutions=(),
            target_instance_mapping_required=True,
        )

        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=CHARACTER_ID,
            edited_values={},
            units={"DefIgnore": 0.01},
        )[0]

        self.assertEqual(result.baseline_damage, result.predicted_damage)
        self.assertEqual(0.0, result.coverage_percent)
        self.assertIn("0% 表示未量化", result.assumption)

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
