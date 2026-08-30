# 验证五觉、多单位、零值候选与九原独立附着物公式的公共回归。
from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace

from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleDamageQuantification,
    BattleQuantificationGap,
)
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleHitReplayTerm,
    BattleMaxHpReductionEvent,
    BattleTargetCondition,
)
from src.services.battle_marginal_calculation_service import (
    BattleMarginalCalculationService,
)


CHARACTER_ID = 1072


def _baseline() -> BattleCharacterBaseline:
    values = {
        "AtkBase": 1000.0,
        "AtkUp": 0.0,
        "AtkAdd": 0.0,
        "CritBase": 0.5,
        "CritDamageBase": 1.0,
        "DamageUpGeneralBase": 0.0,
    }
    return BattleCharacterBaseline(
        character_id=CHARACTER_ID,
        character_name="测试角色",
        source="fixture",
        stats=tuple(
            BattleCharacterStat(key, key, value, key != "AtkBase")
            for key, value in values.items()
        ),
    )


def _hit(
    event_id: str = "hit:1",
    *,
    classification: str = "direct",
    effect: str = "",
    attribute: str = "nature",
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=1,
        relative_time_us=1_000_000,
        character_id=CHARACTER_ID,
        character_name="测试角色",
        skill_name="测试技能",
        damage_name="测试伤害",
        damage_component="skill",
        attack_type="skill",
        damage_attribute=attribute,
        target_id="target:1",
        target_name="目标",
        damage=1000.0,
        direction="outgoing",
        is_follow_up=False,
        classification=classification,
        gameplay_effect_id=effect,
    )


def _replay(hit: BattleAnalysisHit, *, policy: str = "character") -> BattleHitReplayResult:
    scaling = BattleHitReplayFactor(
        factor_id="scaling",
        label="Atk 乘区",
        value=1000.0,
        evidence_basis="fixture",
        terms=(BattleHitReplayTerm(
            term_id="scaling:AtkBase",
            property_id="AtkBase",
            label="AtkBase",
            value=1000.0,
            source_group="panel",
            source_name="fixture",
            is_percent=False,
            evidence_basis="fixture",
        ),),
    )
    return BattleHitReplayResult(
        event_id=hit.event_id,
        observed_damage=hit.damage,
        non_critical_damage=hit.damage,
        critical_damage=hit.damage * 2.0,
        selected_damage=hit.damage,
        selected_error_percent=0.0,
        critical_state="ambiguous",
        confidence="高",
        factors=(scaling,),
        critical_rate=0.5 if policy == "fixed" else 0.85,
        expected_damage=hit.damage,
        critical_policy=policy,
    )


def _analysis(
    hit: BattleAnalysisHit,
    replay: BattleHitReplayResult,
    *,
    vital: BattleMaxHpReductionEvent | None = None,
    target_condition: BattleTargetCondition | None = None,
):
    vital_damage = 0.0 if vital is None else vital.effective_hp_loss
    return SimpleNamespace(
        baselines=(_baseline(),),
        hits=(hit,),
        hit_replays=(replay,),
        buff_intervals=(),
        roles=(SimpleNamespace(
            character_id=CHARACTER_ID,
            max_hp_reduction_damage=vital_damage,
        ),),
        effective_damage=hit.damage + vital_damage,
        build_counterfactual=None,
        target_condition=target_condition,
        target_conditions_by_half=(),
        target_instance_resolutions=(),
        target_instance_mapping_required=False,
        max_hp_events=(() if vital is None else (vital,)),
    )


def _vital(hit: BattleAnalysisHit) -> BattleMaxHpReductionEvent:
    return BattleMaxHpReductionEvent(
        event_id="max-hp:1",
        target_id=hit.target_id,
        target_name=hit.target_name,
        observed_at_us=hit.relative_time_us,
        old_max_hp=10_000.0,
        new_max_hp=9_000.0,
        max_hp_reduction=1_000.0,
        hp_before_settlement=8_000.0,
        hp_ratio_before=0.8,
        effective_hp_loss=800.0,
        source_character_id=CHARACTER_ID,
        source_character_name=hit.character_name,
        mechanic_kind="lacrimosa_nightmare_awaken_5",
        mechanic_name="安魂曲五觉",
        source_skill_name="噩梦",
        evidence_event_ids=(hit.event_id,),
        attribution_confidence="高",
        calculation_confidence="高",
        inference_basis="fixture",
    )


class BattleMarginalRegressionTests(unittest.TestCase):
    def test_five_awaken_can_calculate_multiple_default_units(self) -> None:
        hit = _hit(
            "nightmare:1",
            classification="dot",
            effect="GE_Player_Lacrimosa_Blood_Damage",
            attribute="chaos",
        )
        replay = _replay(hit, policy="fixed")
        analysis = _analysis(hit, replay, vital=_vital(hit))
        units = BattleMarginalCalculationService.default_units(
            analysis.baselines[0],
            hits=analysis.hits,
            replays={replay.event_id: replay},
        )
        rows = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=CHARACTER_ID,
            edited_values={},
            units=units,
        )

        property_ids = {row.property_id for row in rows}
        self.assertEqual(set(units), property_ids)
        self.assertIn("CritDamageBase", property_ids)
        self.assertIn("DamageUpChaosBase", property_ids)

    def test_awaken_five_rejects_ordinary_direct_as_source(self) -> None:
        hit = _hit(
            "ordinary:1",
            effect="GE_Other_Lacrimosa_Blood_Damage_Fake",
        )
        result = BattleMarginalCalculationService.calculate(
            analysis=_analysis(hit, _replay(hit), vital=_vital(hit)),
            character_id=CHARACTER_ID,
            edited_values={},
            units={"CritBase": 0.01},
        )[0]

        self.assertEqual("partial", result.quantification.status)
        self.assertIn(
            "linked_source_hit_missing",
            {gap.code for gap in result.quantification.gaps},
        )

    def test_default_units_include_zero_formal_nature_penetration(self) -> None:
        hit = _hit()
        base = _baseline()
        baseline = BattleCharacterBaseline(
            character_id=base.character_id,
            character_name=base.character_name,
            source=base.source,
            stats=tuple((*base.stats,
                BattleCharacterStat("DamageUpCosmosBase", "光伤", 0.2, True),
                BattleCharacterStat("DamagePenetrateCosmos", "光穿", 0.1, True),
            )),
        )
        units = BattleMarginalCalculationService.default_units(
            baseline,
            hits=(hit,),
            replays={hit.event_id: _replay(hit)},
        )

        self.assertEqual(0.01, units["DamagePenetrateNature"])
        self.assertIn("DamageUpNatureBase", units)
        self.assertNotIn("DamageUpCosmosBase", units)
        self.assertNotIn("DamagePenetrateCosmos", units)

    def test_kuhara_attachment_uses_independent_nature_attack_formula(self) -> None:
        hit = _hit(
            classification="attachment",
            effect="GE_Player_Kuhara_Seed_Damage",
            attribute="",
        )
        condition = BattleTargetCondition(
            target_name="目标",
            enemy_level=90.0,
            scene="outer_realm",
            defense_reduction=0.0,
            vulnerability=0.0,
            resistances=(("nature", 0.2),),
            enemy_defense_base=900.0,
        )
        rows = BattleMarginalCalculationService.calculate(
            analysis=_analysis(hit, _replay(hit), target_condition=condition),
            character_id=CHARACTER_ID,
            edited_values={},
            units={
                "AtkUp": 0.01,
                "CritBase": 0.01,
                "DamageUpNatureBase": 0.01,
                "DamagePenetrateNature": 0.01,
            },
        )

        self.assertTrue(all(row.quantification.status == "complete" for row in rows))
        self.assertTrue(all(row.full_role_gain_percent > 0.0 for row in rows))

    def test_kuhara_nature_override_is_limited_to_four_formal_effects(self) -> None:
        supported = (
            "GE_Player_Kuhara_Seed_Damage",
            "GE_Player_Kuhara_BudBoom_Damage",
            "GE_Player_Kuhara_BudEnd_Damage",
            "GE_Player_Kuhara_SeedReaction_Damage",
        )
        for effect in supported:
            hit = _hit(classification="attachment", effect=effect, attribute="")
            units = BattleMarginalCalculationService.default_units(
                _baseline(),
                hits=(hit,),
                replays={hit.event_id: _replay(hit)},
            )
            self.assertIn("DamageUpNatureBase", units)
            self.assertIn("DamagePenetrateNature", units)

        unrelated = _hit(
            classification="attachment",
            effect="GE_Player_Other_Attachment_Damage",
            attribute="",
        )
        units = BattleMarginalCalculationService.default_units(
            _baseline(),
            hits=(unrelated,),
            replays={unrelated.event_id: _replay(unrelated)},
        )
        self.assertNotIn("DamageUpNatureBase", units)
        self.assertNotIn("DamagePenetrateNature", units)

    def test_creation_defense_margin_uses_replay_formula_attribute(self) -> None:
        hit = replace(
            _hit(
                classification="reaction",
                effect="GE_ActorReaction_1_Damage",
                attribute="normal",
            ),
            damage_name="创生",
        )
        replay = replace(
            _replay(hit, policy="disabled"),
            formula_type="创生",
            formula_damage_attribute="cosmos",
        )
        condition = BattleTargetCondition(
            target_name="目标",
            enemy_level=90.0,
            scene="outer_realm",
            defense_reduction=0.0,
            vulnerability=0.0,
            resistances=(("cosmos", 0.2),),
            enemy_defense_base=900.0,
        )

        result = BattleMarginalCalculationService.calculate(
            analysis=_analysis(hit, replay, target_condition=condition),
            character_id=CHARACTER_ID,
            edited_values={},
            units={"DefIgnore": 0.01},
        )[0]

        self.assertEqual("complete", result.quantification.status)
        self.assertGreater(result.quantification.quantified_increment, 0.0)

    def test_partial_current_candidate_denominator_stays_partial(self) -> None:
        hit = _hit()
        analysis = _analysis(hit, _replay(hit))
        gap = BattleQuantificationGap(
            code="fixture_gap",
            dimension_id="current_build",
            dependency_scope="mechanic_specific",
            property_ids=(),
            explanation="当前候选仍有未量化逐击",
        )
        hit_ratio = BattleCounterfactualRatio.partial(
            1.2,
            method="fixture",
            confidence="低",
            dependency_scope="mechanic_specific",
            included_dimension_ids=("known_component",),
            cancelled_dimension_ids=(),
            gaps=(gap,),
            explanation="fixture",
        )
        denominator = BattleDamageQuantification.from_buckets(
            status="partial",
            fully_quantified_damage=0.0,
            partially_quantified_damage=1200.0,
            unavailable_damage=0.0,
            proven_unchanged_damage=0.0,
            quantified_increment=200.0,
            gaps=(gap,),
        )
        analysis.build_counterfactual = SimpleNamespace(
            hits=(SimpleNamespace(
                event_id=hit.event_id,
                candidate_damage=None,
                known_projection_damage=1200.0,
                quantification=hit_ratio,
            ),),
            roles=(SimpleNamespace(
                character_id=CHARACTER_ID,
                candidate_damage=None,
                known_projection_damage=1200.0,
                quantification=denominator,
            ),),
            candidate_damage=None,
            known_projection_damage=1200.0,
            quantification=denominator,
            vital_events=(),
        )

        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=CHARACTER_ID,
            edited_values={},
            units={"AtkUp": 0.01},
        )[0]

        self.assertEqual("partial", result.quantification.status)
        self.assertEqual("partial", result.role_denominator_status)
        self.assertEqual("partial", result.team_denominator_status)
        self.assertIsNotNone(result.quantified_role_gain_percent)
        self.assertIsNone(result.full_role_gain_percent)
        self.assertIsNone(result.full_team_gain_percent)

    def test_current_vital_state_continues_from_saved_candidate_axis(self) -> None:
        hit = _hit(
            "nightmare:current",
            classification="dot",
            effect="GE_Player_Lacrimosa_Blood_Damage",
            attribute="chaos",
        )
        analysis = _analysis(hit, _replay(hit, policy="fixed"), vital=_vital(hit))
        hit_ratio = BattleCounterfactualRatio.complete(
            1.2,
            method="fixture_current_hit",
            confidence="高",
            dependency_scope="target_sensitive",
            included_dimension_ids=("structured_formula",),
            explanation="fixture",
        )
        denominator = BattleDamageQuantification.from_buckets(
            status="complete",
            fully_quantified_damage=2136.0,
            quantified_increment=336.0,
        )
        analysis.build_counterfactual = SimpleNamespace(
            hits=(SimpleNamespace(
                event_id=hit.event_id,
                candidate_damage=1200.0,
                known_projection_damage=1200.0,
                quantification=hit_ratio,
            ),),
            roles=(SimpleNamespace(
                character_id=CHARACTER_ID,
                candidate_damage=2136.0,
                known_projection_damage=2136.0,
                quantification=denominator,
            ),),
            candidate_damage=2136.0,
            known_projection_damage=2136.0,
            quantification=denominator,
            vital_events=(SimpleNamespace(
                event_id="max-hp:1",
                candidate_damage=936.0,
                known_projection_damage=936.0,
                quantification=hit_ratio,
                candidate_state=(10_000.0, 7800.0, 1200.0),
            ),),
        )

        result = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=CHARACTER_ID,
            edited_values={},
            units={"AtkUp": 0.01},
        )[0]

        self.assertEqual("complete", result.quantification.status)
        self.assertAlmostEqual(2155.9056, result.known_projection_damage)
        self.assertNotIn(
            "current_vital_sequence_state_unavailable",
            {gap.code for gap in result.quantification.gaps},
        )


if __name__ == "__main__":
    unittest.main()
