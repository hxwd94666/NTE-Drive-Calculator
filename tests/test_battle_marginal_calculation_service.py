# 验证属性单位边际按逐击暴击策略和已有团队倾陷贡献计算。
from __future__ import annotations

import unittest
from dataclasses import dataclass, replace
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
    max_hp_events: tuple = ()


def _baseline() -> BattleCharacterBaseline:
    return BattleCharacterBaseline(
        character_id=CHARACTER_ID,
        character_name="灵可",
        source="frozen-test",
        stats=(
            BattleCharacterStat("AtkBase", "基础攻击力", 1000.0, False),
            BattleCharacterStat("AtkUp", "攻击力提升", 0.0, True),
            BattleCharacterStat("AtkAdd", "固定攻击力", 0.0, False),
            BattleCharacterStat("HPMaxBase", "基础生命值", 2000.0, False),
            BattleCharacterStat("HPMaxUp", "生命值提升", 0.0, True),
            BattleCharacterStat("HPMaxAdd", "固定生命值", 0.0, False),
            BattleCharacterStat("DefBase", "基础防御力", 500.0, False),
            BattleCharacterStat("DefUp", "防御力提升", 0.0, True),
            BattleCharacterStat("DefAdd", "固定防御力", 0.0, False),
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


def _scaling_factor(scaling_id: str) -> BattleHitReplayFactor:
    base_id = {
        "Atk": "AtkBase",
        "HPMax": "HPMaxBase",
        "Def": "DefBase",
    }[scaling_id]
    return BattleHitReplayFactor(
        factor_id="scaling",
        label=f"{scaling_id} 乘区",
        value=1000.0,
        evidence_basis="fixture",
        terms=(BattleHitReplayTerm(
            term_id=f"scaling:{base_id}",
            property_id=base_id,
            label=base_id,
            value=1000.0,
            source_group="panel",
            source_name="fixture",
            is_percent=False,
            evidence_basis="fixture",
        ),),
    )


def _critical_replay(
    hit,
    policy: str,
    rate: float | None,
    *,
    scaling_id: str | None = "Atk",
):
    return BattleHitReplayResult(
        event_id=hit.event_id,
        observed_damage=hit.damage,
        non_critical_damage=hit.damage,
        critical_damage=hit.damage * 2.0,
        selected_damage=hit.damage,
        selected_error_percent=0.0,
        critical_state=("not_applicable" if policy == "disabled" else "ambiguous"),
        confidence="高",
        factors=(() if scaling_id is None else (_scaling_factor(scaling_id),)),
        critical_rate=rate,
        expected_damage=None if policy == "unknown" else hit.damage,
        critical_policy=policy,
    )


class BattleMarginalCalculationServiceTests(unittest.TestCase):
    def test_hp_and_def_scaling_margins_use_replay_scaling_terms(self) -> None:
        hit = _hit()
        for scaling_id, property_id in (("HPMax", "HPMaxUp"), ("Def", "DefUp")):
            with self.subTest(scaling_id=scaling_id):
                analysis = _analysis(
                    hit,
                    _critical_replay(
                        hit,
                        "character",
                        0.5,
                        scaling_id=scaling_id,
                    ),
                )
                result = BattleMarginalCalculationService.calculate(
                    analysis=analysis,
                    character_id=CHARACTER_ID,
                    edited_values={},
                    units={property_id: 0.1},
                )[0]

                self.assertEqual("complete", result.quantification.status)
                self.assertAlmostEqual(1100.0, result.known_projection_damage)
                self.assertAlmostEqual(10.0, result.full_role_gain_percent)

    def test_unknown_scaling_margin_is_unavailable_not_zero(self) -> None:
        hit = _hit()
        analysis = _analysis(
            hit,
            _critical_replay(hit, "character", 0.5, scaling_id=None),
        )

        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=CHARACTER_ID,
            edited_values={},
            units={"AtkUp": 0.1},
        )[0]

        self.assertEqual("unavailable", result.quantification.status)
        self.assertIsNone(result.known_projection_damage)
        self.assertIsNone(result.quantified_role_gain_percent)
        self.assertIsNone(result.full_role_gain_percent)

    def test_attack_margin_follows_formally_linked_weave_source_hit(self) -> None:
        primary = replace(_hit(event_id="7:primary", damage=1000.0), sequence=7)
        weave = replace(
            _hit(
                event_id="7:follow_up",
                classification="weave",
                damage=300.0,
            ),
            sequence=7,
            is_follow_up=True,
        )
        primary_replay = _critical_replay(primary, "character", 0.5)
        weave_replay = _critical_replay(
            weave,
            "disabled",
            0.0,
            scaling_id=None,
        )
        analysis = _analysis(primary, primary_replay)
        analysis.hits = (primary, weave)
        analysis.hit_replays = (primary_replay, weave_replay)
        analysis.effective_damage = 1300.0

        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=CHARACTER_ID,
            edited_values={},
            units={"AtkUp": 0.1},
        )[0]

        self.assertEqual("complete", result.quantification.status)
        self.assertEqual(1300.0, result.quantification.fully_quantified_damage)
        self.assertAlmostEqual(1430.0, result.known_projection_damage)
        self.assertAlmostEqual(10.0, result.full_role_gain_percent)

    def test_attack_margin_does_not_guess_an_unlinked_weave_source(self) -> None:
        weave = replace(
            _hit(classification="weave", damage=300.0),
            is_follow_up=True,
        )
        analysis = _analysis(
            weave,
            _critical_replay(weave, "disabled", 0.0, scaling_id=None),
        )

        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=CHARACTER_ID,
            edited_values={},
            units={"AtkUp": 0.1},
        )[0]

        self.assertEqual("unavailable", result.quantification.status)
        self.assertIsNone(result.full_role_gain_percent)

    def test_ring_strength_includes_structured_reaction_consumers(self) -> None:
        for name, effect_id in (
            ("创生花", "GE_ActorReaction_1_Damage"),
            ("浊燃", "Buff_Reaction_5_new_1036"),
            ("黯星", "Buff_Reaction_4_new"),
        ):
            with self.subTest(name=name):
                hit = replace(
                    _hit(classification="reaction", damage=600.0),
                    damage_name=name,
                    gameplay_effect_id=effect_id,
                )
                replay = BattleHitReplayResult(
                    event_id=hit.event_id,
                    observed_damage=hit.damage,
                    non_critical_damage=hit.damage,
                    critical_damage=None,
                    selected_damage=hit.damage,
                    selected_error_percent=0.0,
                    critical_state="not_applicable",
                    confidence="高",
                    factors=(BattleHitReplayFactor(
                        factor_id="scaling",
                        label="环合强度区",
                        value=1.0,
                        evidence_basis=f"正式{name}公式",
                    ),),
                    critical_policy="disabled",
                )
                analysis = _analysis(hit, replay)

                result = BattleMarginalCalculationService.calculate(
                    analysis=analysis,
                    character_id=CHARACTER_ID,
                    edited_values={},
                    units={"MagBase": 6.0},
                )[0]

                self.assertEqual("complete", result.quantification.status)
                self.assertEqual(
                    600.0,
                    result.quantification.fully_quantified_damage,
                )

    def test_ring_strength_does_not_guess_unstructured_reaction_damage(self) -> None:
        hit = replace(
            _hit(classification="reaction", damage=600.0),
            damage_name="环合伤害",
        )
        analysis = _analysis(
            hit,
            _critical_replay(hit, "disabled", 0.0, scaling_id=None),
        )

        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=CHARACTER_ID,
            edited_values={},
            units={"MagBase": 6.0},
        )[0]

        self.assertEqual("not_applicable", result.quantification.status)

    def test_ring_strength_excludes_stain_until_a_formal_settlement_exists(self) -> None:
        hit = replace(
            _hit(classification="reaction", damage=600.0),
            damage_name="浸染",
        )
        replay = BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=hit.damage,
            non_critical_damage=hit.damage,
            critical_damage=None,
            selected_damage=hit.damage,
            selected_error_percent=0.0,
            critical_state="not_applicable",
            confidence="低",
            factors=(BattleHitReplayFactor(
                factor_id="scaling",
                label="环合强度区",
                value=1.0,
                evidence_basis="仅有通用静态公式，缺少正式浸染结算战报",
            ),),
            critical_policy="disabled",
        )
        analysis = _analysis(hit, replay)

        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=CHARACTER_ID,
            edited_values={},
            units={"MagBase": 6.0},
        )[0]

        self.assertEqual("not_applicable", result.quantification.status)

    def test_unknown_target_attack_margin_anchors_on_candidate_projection(self) -> None:
        hit = _hit()
        analysis = _analysis(hit, _critical_replay(hit, "character", 0.5))
        analysis.build_counterfactual = SimpleNamespace(
            hits=(
                SimpleNamespace(
                    event_id=hit.event_id,
                    candidate_damage=1500.0,
                    known_projection_damage=1500.0,
                ),
            ),
            roles=(
                SimpleNamespace(
                    character_id=CHARACTER_ID,
                    candidate_damage=1500.0,
                    known_projection_damage=1500.0,
                ),
            ),
            candidate_damage=2000.0,
            known_projection_damage=2000.0,
        )

        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=CHARACTER_ID,
            edited_values={},
            units={"AtkUp": 0.0125},
        )[0]

        self.assertAlmostEqual(1500.0, result.baseline_damage)
        self.assertAlmostEqual(1518.75, result.known_projection_damage)
        self.assertAlmostEqual(1.25, result.quantified_role_gain_percent)
        self.assertAlmostEqual(1.25, result.full_role_gain_percent)
        self.assertAlmostEqual(0.9375, result.quantified_team_gain_percent)
        self.assertAlmostEqual(0.9375, result.full_team_gain_percent)
        self.assertEqual("complete", result.quantification.status)
        self.assertEqual(1500.0, result.quantification.fully_quantified_damage)
        self.assertEqual(0.0, result.quantification.unavailable_damage)
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

        self.assertGreater(result.full_role_gain_percent, 0.0)
        self.assertEqual("complete", result.quantification.status)
        self.assertEqual(1000.0, result.quantification.fully_quantified_damage)

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
            upper_result.known_projection_damage
            - upper_result.baseline_damage
            + lower_result.known_projection_damage
            - lower_result.baseline_damage
        )
        self.assertAlmostEqual(
            expected_increment,
            combined_result.known_projection_damage
            - combined_result.baseline_damage,
        )
        self.assertEqual("complete", combined_result.quantification.status)
        self.assertEqual(
            2000.0,
            combined_result.quantification.fully_quantified_damage,
        )

    def test_one_known_and_one_unknown_target_profile_is_partial(self) -> None:
        upper = _hit(event_id="hit:upper", scope_half="upper", target_id="7")
        lower = _hit(event_id="hit:lower", scope_half="lower", target_id="7")
        upper_condition = _target_condition(defense=600.0, resistance=0.1)
        upper_replay = _critical_replay(upper, "character", 0.5)
        lower_replay = _critical_replay(lower, "character", 0.5)
        analysis = _analysis(
            upper,
            upper_replay,
            target_instance_resolutions=(
                _target_resolution(
                    scope_half="upper",
                    target_id="7",
                    condition=upper_condition,
                ),
            ),
            target_instance_mapping_required=True,
        )
        analysis.hits = (upper, lower)
        analysis.hit_replays = (upper_replay, lower_replay)
        analysis.effective_damage = 2000.0

        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=CHARACTER_ID,
            edited_values={},
            units={"DefIgnore": 0.01},
        )[0]

        self.assertEqual("partial", result.quantification.status)
        self.assertIsNotNone(result.known_projection_damage)
        self.assertIsNotNone(result.quantified_role_gain_percent)
        self.assertIsNone(result.full_role_gain_percent)
        self.assertEqual(1000.0, result.quantification.fully_quantified_damage)
        self.assertEqual(1000.0, result.quantification.unavailable_damage)
        self.assertIn("不代表完整收益", result.assumption)

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

        self.assertIsNone(result.known_projection_damage)
        self.assertIsNone(result.quantified_role_gain_percent)
        self.assertIsNone(result.full_role_gain_percent)
        self.assertEqual("unavailable", result.quantification.status)
        self.assertEqual(1000.0, result.quantification.unavailable_damage)
        self.assertIn("本项未量化", result.assumption)

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
            .full_role_gain_percent,
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
        self.assertEqual("not_applicable", fixed_rate.quantification.status)
        self.assertEqual(0.0, fixed_rate.full_role_gain_percent)
        self.assertEqual(
            fixed_rate.baseline_damage,
            fixed_rate.known_projection_damage,
        )
        self.assertGreater(fixed_damage.full_role_gain_percent, 0.0)

        disabled = BattleMarginalCalculationService.calculate(
            analysis=_analysis(hit, _critical_replay(hit, "disabled", 0.0)),
            character_id=CHARACTER_ID,
            edited_values={},
            units=units,
        )
        self.assertTrue(all(
            row.quantification.status == "not_applicable"
            and row.full_role_gain_percent == 0.0
            for row in disabled
        ))

        unknown = BattleMarginalCalculationService.calculate(
            analysis=_analysis(hit, _critical_replay(hit, "unknown", None)),
            character_id=CHARACTER_ID,
            edited_values={},
            units=units,
        )
        self.assertTrue(all(
            row.quantification.status == "unavailable"
            and row.full_role_gain_percent is None
            and row.quantified_role_gain_percent is None
            for row in unknown
        ))

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

        self.assertEqual("complete", result.quantification.status)
        self.assertAlmostEqual(
            2000.0 / 3.0,
            result.quantification.fully_quantified_damage,
        )
        self.assertAlmostEqual(2000.0 / 3.0, result.baseline_damage)
        self.assertAlmostEqual(2000.0 / 3.0 + 10.0, result.known_projection_damage)
        self.assertAlmostEqual(1.5, result.full_role_gain_percent)
        self.assertAlmostEqual(1.0, result.full_team_gain_percent)
        self.assertAlmostEqual(
            result.quantification.basis_damage,
            result.quantification.fully_quantified_damage
            + result.quantification.partially_quantified_damage
            + result.quantification.unavailable_damage
            + result.quantification.proven_unchanged_damage,
        )

    def test_topple_unit_accepts_omitted_zero_strength_term(self) -> None:
        hit = _hit(classification="topple")
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
                    factor_id=f"topple_character:{CHARACTER_ID}",
                    label="灵可倾陷贡献",
                    value=1000.0,
                    evidence_basis="零倾陷强度的逐角色公式",
                ),
                BattleHitReplayFactor(
                    factor_id="topple_character:1001",
                    label="队友倾陷贡献",
                    value=1000.0,
                    evidence_basis="逐角色倾陷公式",
                ),
            ),
            critical_rate=0.0,
            expected_damage=2000.0,
            critical_policy="disabled",
        )
        analysis = _analysis(hit, replay)
        analysis.baselines = (replace(
            analysis.baselines[0],
            stats=tuple(
                replace(row, value=0.0)
                if row.property_id == "UnbalIntensityBase"
                else row
                for row in analysis.baselines[0].stats
            ),
        ),)

        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=CHARACTER_ID,
            edited_values={},
            units={"UnbalIntensityBase": 6.0},
        )[0]

        self.assertEqual("complete", result.quantification.status)
        self.assertEqual(500.0, result.quantification.fully_quantified_damage)
        self.assertAlmostEqual(510.0, result.known_projection_damage)
        self.assertAlmostEqual(2.0, result.full_role_gain_percent)


if __name__ == "__main__":
    unittest.main()
