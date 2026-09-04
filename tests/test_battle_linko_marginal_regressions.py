# 验证灵可混合公式归属、覆纹边际和精确调频覆盖口径。
from __future__ import annotations

import unittest
from dataclasses import dataclass, replace
from types import SimpleNamespace
from unittest.mock import patch

from src.domain.battle_counterfactual_quantification import BattleCounterfactualRatio
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitBuffProjection,
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleHitReplayTerm,
    BattleInferredBuffInterval,
)
from src.services.battle_buff_counterfactual_plan_service import (
    battle_buff_applied_hits,
    battle_buff_counterfactual_key,
)
from src.services.battle_buff_counterfactual_service import (
    BattleBuffCounterfactualService,
)
from src.services.battle_hit_counterfactual_ratio_service import (
    BattleHitCounterfactualRatioService,
)
from src.services.battle_hit_replay_service import BattleHitReplayService
from src.services.battle_marginal_calculation_service import (
    BattleMarginalCalculationService,
)


LINKO_ID = 1072


@dataclass
class _Analysis:
    baselines: tuple
    hits: tuple
    hit_replays: tuple
    buff_intervals: tuple = ()
    roles: tuple = ()
    effective_damage: float = 0.0
    build_counterfactual: object | None = None
    target_condition: object | None = None
    target_conditions_by_half: tuple = ()
    target_instance_resolutions: tuple = ()
    target_instance_mapping_required: bool = False
    max_hp_events: tuple = ()
    buff_counterfactuals: tuple = ()
    range_start_us: int = 0
    range_end_us: int = 10_000_000


def _baseline() -> BattleCharacterBaseline:
    return BattleCharacterBaseline(
        character_id=LINKO_ID,
        character_name="灵可",
        source="fixture",
        stats=(
            BattleCharacterStat("AtkBase", "基础攻击力", 1000.0, False),
            BattleCharacterStat("AtkUp", "攻击力提升", 0.0, True),
            BattleCharacterStat("AtkAdd", "固定攻击力", 0.0, False),
            BattleCharacterStat("CritBase", "暴击率", 0.5, True),
            BattleCharacterStat("CritDamageBase", "暴击伤害", 1.0, True),
            BattleCharacterStat("DamageUpGeneralBase", "通用增伤", 0.0, True),
            BattleCharacterStat("DamageUpNatureBase", "灵属性增伤", 0.0, True),
            BattleCharacterStat("DamagePenetrateNature", "灵属性穿透", 0.0, True),
            BattleCharacterStat("MagBase", "环合强度", 36.0, False),
        ),
    )


def _hit(
    event_id: str,
    *,
    character_id: int,
    damage: float,
    classification: str = "direct",
    follow_up: bool = False,
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=1,
        relative_time_us=1_000_000,
        character_id=character_id,
        character_name="灵可" if character_id == LINKO_ID else "残虹",
        skill_name="测试 QTE",
        damage_name="测试伤害",
        damage_component="follow_up" if follow_up else "skill",
        attack_type="QTE",
        damage_attribute="incantation",
        target_id="target:1",
        target_name="目标",
        damage=damage,
        direction="outgoing",
        is_follow_up=follow_up,
        classification=classification,
    )


def _scaling_factor() -> BattleHitReplayFactor:
    return BattleHitReplayFactor(
        factor_id="scaling",
        label="攻击力乘区",
        value=1000.0,
        evidence_basis="fixture",
        terms=(BattleHitReplayTerm(
            term_id="scaling:AtkBase",
            property_id="AtkBase",
            label="基础攻击力",
            value=1000.0,
            source_group="panel",
            source_name="fixture",
            is_percent=False,
            evidence_basis="fixture",
        ),),
    )


def _direct_replay(hit: BattleAnalysisHit) -> BattleHitReplayResult:
    return BattleHitReplayResult(
        event_id=hit.event_id,
        observed_damage=hit.damage,
        non_critical_damage=hit.damage,
        critical_damage=hit.damage * 2.0,
        selected_damage=hit.damage,
        selected_error_percent=0.0,
        critical_state="ambiguous",
        confidence="高",
        factors=(_scaling_factor(),),
        critical_rate=0.5,
        expected_damage=hit.damage,
        critical_policy="character",
        formula_damage_attribute="incantation",
        formula_action_character_id=hit.character_id,
        formula_definition_owner_character_id=hit.character_id,
        formula_panel_character_id=LINKO_ID,
        formula_skill_level_character_id=LINKO_ID,
        formula_skill_level_ability_id="GA_Radio072_QTE",
        formula_context_kind="linko_coattack:skill",
        formula_context_confidence="中",
        formula_is_formal_follow_up=True,
        formula_target_has_weave=True,
    )


def _weave_replay(hit: BattleAnalysisHit) -> BattleHitReplayResult:
    strength = 1.0 + 0.20 * 36.0 / (36.0 + 180.0)
    return BattleHitReplayResult(
        event_id=hit.event_id,
        observed_damage=hit.damage,
        non_critical_damage=hit.damage,
        critical_damage=None,
        selected_damage=hit.damage,
        selected_error_percent=0.0,
        critical_state="not_applicable",
        confidence="高",
        factors=(
            BattleHitReplayFactor(
                "recorded_direct_damage", "原伤害", 1000.0, "fixture"
            ),
            BattleHitReplayFactor(
                "weave_strength", "覆纹环合强度区", strength, "fixture"
            ),
            BattleHitReplayFactor(
                "weave_followup", "覆纹追加倍率", 1.30 * strength - 1.0, "fixture"
            ),
        ),
        formula_type="覆纹",
        critical_policy="disabled",
    )


def _topple_replay(hit: BattleAnalysisHit) -> BattleHitReplayResult:
    return BattleHitReplayResult(
        event_id=hit.event_id,
        observed_damage=hit.damage,
        non_critical_damage=None,
        critical_damage=None,
        selected_damage=None,
        selected_error_percent=None,
        critical_state="unreplayable",
        confidence="中",
        factors=(
            BattleHitReplayFactor(
                "topple_character:1072", "灵可倾陷贡献", 104.0, "fixture"
            ),
            BattleHitReplayFactor(
                "topple_character:1036", "残虹倾陷贡献", 116.0, "fixture"
            ),
        ),
        formula_type="倾陷",
        critical_policy="unknown",
    )


def _analysis(*hits_and_replays) -> _Analysis:
    hits = tuple(row[0] for row in hits_and_replays)
    replays = tuple(row[1] for row in hits_and_replays)
    return _Analysis(
        baselines=(_baseline(),),
        hits=hits,
        hit_replays=replays,
        roles=(SimpleNamespace(
            character_id=LINKO_ID,
            total_damage=sum(hit.damage for hit in hits if hit.character_id == LINKO_ID),
            max_hp_reduction_damage=0.0,
        ),),
        effective_damage=sum(hit.damage for hit in hits),
    )


def _precision_interval(property_id: str) -> BattleInferredBuffInterval:
    return BattleInferredBuffInterval(
        interval_id=f"precision:{property_id}",
        buff_asset_path="character_passive:1072:GA_Radio072_Passive_2",
        buff_name=f"精确调频·{property_id}",
        source_effect_definition_id="PASSIVE-1072-GA_Radio072_Passive_2",
        source_kind="derived_linko_coattack_inference",
        source_character_id=LINKO_ID,
        source_character_name="灵可",
        target_scope="target",
        start_us=1,
        end_us=12_000_001,
        stacks=1,
        duration_policy="active_time",
        state_confidence="中",
        value_confidence="高",
        inference_basis="fixture",
        trigger_event_type="INFERRED_LINKO_COATTACK",
        evidence_action_ids=(),
        evidence_event_ids=(),
        modifiers=(BattleBuffModifierEvidence(
            property_id=property_id,
            modifier_operation="EGameplayModOp::Additive",
            magnitude_kind="confirmed_character_passive",
            magnitude_value=-0.08,
            calculation_asset_path="",
            value_confidence="高",
        ),),
        target_id="target:1",
    )


class BattleLinkoMarginalRegressionTests(unittest.TestCase):
    def test_team_topple_is_not_a_linko_critical_consumer(self) -> None:
        own = _hit("1:primary", character_id=LINKO_ID, damage=500.0)
        qte = _hit("2:primary", character_id=1036, damage=300.0)
        topple = replace(
            _hit("3:primary", character_id=LINKO_ID, damage=220.0),
            classification="reaction",
            gameplay_effect_id="Buff_Tenacity_Damage",
            damage_name="倾陷伤害",
        )

        result = BattleMarginalCalculationService.calculate(
            analysis=_analysis(
                (own, _direct_replay(own)),
                (qte, _direct_replay(qte)),
                (topple, _topple_replay(topple)),
            ),
            character_id=LINKO_ID,
            edited_values={},
            units={"CritBase": 0.01},
        )[0]

        self.assertEqual("complete", result.quantification.status)
        self.assertAlmostEqual(904.0, result.quantification.basis_damage)
        self.assertAlmostEqual(800.0, result.quantification.fully_quantified_damage)
        self.assertAlmostEqual(104.0, result.quantification.proven_unchanged_damage)
        self.assertAlmostEqual(
            result.quantification.basis_damage,
            result.quantification.fully_quantified_damage
            + result.quantification.partially_quantified_damage
            + result.quantification.unavailable_damage
            + result.quantification.proven_unchanged_damage,
        )

    def test_team_topple_uses_formula_contributor_instead_of_raw_owner(self) -> None:
        topple = replace(
            _hit("1:primary", character_id=1036, damage=220.0),
            classification="reaction",
            gameplay_effect_id="Buff_Tenacity_Damage",
            damage_name="倾陷伤害",
        )
        replay = replace(
            _topple_replay(topple),
            critical_state="not_applicable",
            factors=(
                replace(
                    _topple_replay(topple).factors[0],
                    terms=(BattleHitReplayTerm(
                        "topple:1072:UnbalIntensityBase",
                        "UnbalIntensityBase",
                        "倾陷强度",
                        100.0,
                        "panel",
                        "fixture",
                        False,
                        "fixture",
                    ),),
                ),
                _topple_replay(topple).factors[1],
            ),
        )

        result = BattleMarginalCalculationService.calculate(
            analysis=_analysis((topple, replay)),
            character_id=LINKO_ID,
            edited_values={},
            units={"UnbalIntensityBase": 6.0},
        )[0]

        self.assertEqual("complete", result.quantification.status)
        self.assertAlmostEqual(104.0, result.quantification.basis_damage)
        self.assertAlmostEqual(104.0, result.quantification.fully_quantified_damage)
        self.assertGreater(result.known_projection_damage or 0.0, 104.0)

    def test_linko_panel_drives_teammate_qte_and_element_units(self) -> None:
        qte = _hit("1:primary", character_id=1036, damage=1000.0)
        replay = _direct_replay(qte)
        analysis = _analysis((qte, replay))

        units = BattleMarginalCalculationService.default_units(
            analysis.baselines[0],
            hits=analysis.hits,
            replays={replay.event_id: replay},
        )
        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=LINKO_ID,
            edited_values={},
            units={"AtkUp": 0.10},
        )[0]

        self.assertIn("DamageUpIncantationBase", units)
        self.assertIn("DamagePenetrateIncantation", units)
        self.assertEqual("complete", result.quantification.status)
        self.assertEqual(
            0.0,
            sum(hit.damage for hit in analysis.hits if hit.character_id == LINKO_ID),
        )
        self.assertEqual(1000.0, result.baseline_damage)
        self.assertAlmostEqual(1100.0, result.known_projection_damage)
        self.assertAlmostEqual(10.0, result.full_role_gain_percent)

    def test_linko_panel_change_also_scales_qte_attached_weave_base(self) -> None:
        qte = _hit("1:primary", character_id=1036, damage=1000.0)
        weave = _hit(
            "1:follow_up",
            character_id=1036,
            damage=300.0,
            classification="weave",
            follow_up=True,
        )
        result = BattleMarginalCalculationService.calculate(
            analysis=_analysis((qte, _direct_replay(qte)), (weave, _weave_replay(weave))),
            character_id=LINKO_ID,
            edited_values={},
            units={"AtkUp": 0.10},
        )[0]

        self.assertEqual("complete", result.quantification.status)
        self.assertAlmostEqual(1430.0, result.known_projection_damage)
        self.assertAlmostEqual(10.0, result.full_role_gain_percent)

    def test_weave_magbase_keeps_weave_replay_and_source_projection(self) -> None:
        primary = _hit("1:primary", character_id=LINKO_ID, damage=1000.0)
        weave = _hit(
            "1:follow_up",
            character_id=LINKO_ID,
            damage=300.0,
            classification="weave",
            follow_up=True,
        )
        result = BattleMarginalCalculationService.calculate(
            analysis=_analysis(
                (primary, _direct_replay(primary)),
                (weave, _weave_replay(weave)),
            ),
            character_id=LINKO_ID,
            edited_values={},
            units={"MagBase": 6.0},
        )[0]

        self.assertEqual("complete", result.quantification.status)
        self.assertEqual(300.0, result.quantification.fully_quantified_damage)
        self.assertGreater(result.quantification.quantified_increment or 0.0, 0.0)

    def test_precision_tuning_groups_elements_and_counts_applied_hits(self) -> None:
        chaos = _precision_interval("DamageResistChaosBase")
        incantation = _precision_interval("DamageResistIncantationBase")
        first = _hit("1:primary", character_id=1036, damage=100.0)
        second = _hit("2:primary", character_id=1036, damage=200.0)
        projections = {
            first.event_id: BattleHitBuffProjection(
                event_id=first.event_id,
                modifiers=(),
                applied_interval_ids=(chaos.interval_id,),
                excluded_interval_ids=(),
                exclusion_reasons=(),
                confidence="高",
            ),
            second.event_id: BattleHitBuffProjection(
                event_id=second.event_id,
                modifiers=(),
                applied_interval_ids=(),
                excluded_interval_ids=(chaos.interval_id,),
                exclusion_reasons=("元素不匹配",),
                confidence="高",
            ),
        }

        self.assertNotEqual(
            battle_buff_counterfactual_key(chaos),
            battle_buff_counterfactual_key(incantation),
        )
        self.assertEqual(
            (first,),
            battle_buff_applied_hits((first, second), projections, (chaos,)),
        )

    def test_wrong_element_does_not_keep_an_unrelated_replay_ratio(self) -> None:
        hit = _hit("1:primary", character_id=1036, damage=900.0)
        analysis = _analysis((hit, _direct_replay(hit)))
        analysis.buff_intervals = (_precision_interval("DamageResistNatureBase"),)
        raw_ratio = BattleCounterfactualRatio.complete(
            1.2,
            method="fixture_unrelated_ratio",
            confidence="高",
            dependency_scope="target_sensitive",
            included_dimension_ids=("target_resistance",),
            explanation="fixture",
        )

        with (
            patch.object(
                BattleHitCounterfactualRatioService,
                "compare",
                return_value=raw_ratio,
            ),
            patch.object(BattleHitReplayService, "replay", return_value=()),
        ):
            (result,) = BattleBuffCounterfactualService.calculate(analysis, ())

        self.assertEqual("not_applicable", result.quantification.status)
        self.assertEqual(0, result.affected_hits)
        self.assertEqual(0, result.quantified_hits)
        self.assertEqual(0.0, result.damage_gain)
        self.assertEqual(900.0, result.quantification.proven_unchanged_damage)


if __name__ == "__main__":
    unittest.main()
