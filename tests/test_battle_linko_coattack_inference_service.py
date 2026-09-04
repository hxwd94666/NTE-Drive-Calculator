# 验证灵可同频合击只由完整动作、独立配对和带来源时停证据保守推断。
from __future__ import annotations

import unittest

from src.domain.battle_report import BattleAnalysisHit, BattleInferredAction
from src.services.battle_action_inference_service import BattleActionAnimationCandidate
from src.services.battle_linko_coattack_inference_service import (
    LINKO_COATTACK_INFERENCE_MODEL_VERSION,
    BattleLinkoCoattackInferenceService,
    BattleLinkoType6Evidence,
)
from src.services.battle_time_stop_projection_service import BattleTimeStopProjection


def _hit(
    sequence: int,
    time_us: int,
    *,
    character_id: int,
    ability_id: str,
    effect_id: str,
    target_id: str = "target-1",
    damage_attribute: str = "unknown",
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=f"{sequence}:primary",
        sequence=sequence,
        relative_time_us=time_us,
        character_id=character_id,
        character_name={1003: "早雾", 1036: "残虹", 1072: "灵可"}.get(
            character_id,
            "角色",
        ),
        skill_name=ability_id,
        damage_name=effect_id,
        damage_component="skill",
        attack_type="QTE" if "QTE" in ability_id else "E技能",
        damage_attribute=damage_attribute,
        target_id=target_id,
        target_name="目标",
        damage=100.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        ability_id=ability_id,
        gameplay_effect_id=effect_id,
    )


def _action(
    action_id: str,
    input_kind: str,
    character_id: int,
    hits: tuple[BattleAnalysisHit, ...],
) -> BattleInferredAction:
    return BattleInferredAction(
        action_id=action_id,
        character_id=character_id,
        character_name=hits[0].character_name,
        action_name=hits[0].skill_name,
        input_kind=input_kind,
        input_sequence=input_kind,
        start_us=hits[0].relative_time_us,
        end_us=hits[-1].relative_time_us + 1,
        hits=len(hits),
        damage=sum(hit.damage for hit in hits),
        identity_confidence="中",
        timing_confidence="低",
        inference_basis="测试动作",
        evidence_event_ids=tuple(hit.event_id for hit in hits),
        gameplay_effect_ids=tuple(hit.gameplay_effect_id for hit in hits),
    )


def _linko_e(
    *,
    sequence: int = 1,
    start_us: int = 1_000_000,
    target_id: str = "target-1",
) -> tuple[tuple[BattleAnalysisHit, ...], BattleInferredAction]:
    offsets = (0, 151_000, 358_000, 653_000)
    hits = tuple(
        _hit(
            sequence + ordinal - 1,
            start_us + offsets[ordinal - 1],
            character_id=1072,
            ability_id="GA_Radio072_Skill",
            effect_id=f"GE_Player_Radio072_Skill{ordinal}_Damage",
            target_id=target_id,
        )
        for ordinal in range(1, 5)
    )
    return hits, _action(f"linko-e:{sequence}", "E", 1072, hits)


def _qte(
    *,
    sequence: int = 10,
    time_us: int = 4_200_000,
    character_id: int = 1036,
    target_id: str = "target-1",
    damage_attribute: str = "incantation",
) -> tuple[BattleAnalysisHit, BattleInferredAction]:
    name = "Zankou" if character_id == 1036 else "Sagiri"
    hit = _hit(
        sequence,
        time_us,
        character_id=character_id,
        ability_id=f"GA_{name}_QTE",
        effect_id=f"GE_Player_{name}_QTE_Damage",
        target_id=target_id,
        damage_attribute=damage_attribute,
    )
    return hit, _action(f"qte:{sequence}", "QTE", character_id, (hit,))


def _lte_aoe(
    *,
    sequence: int = 11,
    time_us: int = 4_280_000,
    target_id: str = "target-1",
) -> BattleAnalysisHit:
    return _hit(
        sequence,
        time_us,
        character_id=1072,
        ability_id="GA_Radio072_UltraSkillLTE_AOE",
        effect_id="GE_Player_Radio072_UltralSkillLTE_AOE_Damage",
        target_id=target_id,
        damage_attribute="nature",
    )


def _e_animation() -> BattleActionAnimationCandidate:
    return BattleActionAnimationCandidate(
        ability_id="GA_Radio072_Skill",
        selector_key="Skill1",
        montage_asset_path="/Game/Animation/Radio072_Skill",
        effect_hit_offsets_us=(
            ("GE_Player_Radio072_Skill1_Damage", (130_542,)),
            ("GE_Player_Radio072_Skill2_Damage", (281_632,)),
            ("GE_Player_Radio072_Skill3_Damage", (488_676,)),
            ("GE_Player_Radio072_Skill4_Damage", (783_425,)),
        ),
        trigger_end_offsets_us=(1_991_459,),
        end_event_offsets_us=(),
        section_end_offsets_us=(5_450_000,),
        duration_us=5_450_000,
    )


def _time_stops(
    intervals: tuple[tuple[int, int], ...] = (),
    *,
    source_kind: str = "none",
    confidence: str = "",
    q_action_intervals: tuple[tuple[int, int], ...] = (),
    type6_intervals: tuple[tuple[int, int], ...] = (),
    non_type6_intervals: tuple[tuple[int, int], ...] = (),
) -> BattleTimeStopProjection:
    resolved_non_type6 = (
        non_type6_intervals
        if non_type6_intervals or type6_intervals
        else intervals
    )
    return BattleTimeStopProjection(
        intervals=intervals,
        source_kind=source_kind,
        confidence=confidence,
        inference_basis="测试时停来源",
        q_action_intervals=q_action_intervals,
        type6_intervals=type6_intervals,
        non_type6_intervals=resolved_non_type6,
    )


class BattleLinkoSkillCoattackTests(unittest.TestCase):
    def test_complete_e_uses_full_static_response_window_and_stays_medium(self):
        e_hits, e_action = _linko_e()
        qte, qte_action = _qte(time_us=4_200_000)
        type6 = BattleLinkoType6Evidence(
            event_id="type6:1",
            relative_time_us=2_000_000,
            end_relative_time_us=4_000_000,
            target_id="target-1",
            confidence="高",
            evidence_basis="上游 type6",
        )

        rows = BattleLinkoCoattackInferenceService.infer(
            (*e_hits, qte),
            (e_action, qte_action),
            time_stop_projection=_time_stops(
                ((2_000_000, 4_000_000),),
                source_kind="nte_core",
                confidence="高",
                type6_intervals=((2_000_000, 4_000_000),),
            ),
            animation_candidates=(_e_animation(),),
            type6_evidence=(type6,),
        )

        self.assertEqual("linko-coattack-v1", LINKO_COATTACK_INFERENCE_MODEL_VERSION)
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual("skill", row.trigger_kind)
        self.assertEqual("linko-e:1", row.trigger_action_id)
        self.assertEqual("qte:10", row.qte_action_id)
        self.assertEqual("中", row.confidence)
        self.assertEqual(2_547_000, row.raw_gap_us)
        self.assertEqual(547_000, row.active_gap_us)
        self.assertEqual(1_653_000, row.selection_pause_start_us)
        self.assertEqual(4_200_000, row.selection_pause_end_us)
        self.assertIn("type6:1", row.evidence_event_ids)
        self.assertIn("不能单独证明", row.inference_basis)

    def test_type6_and_qte_without_complete_e_cannot_prove_skill_trigger(self):
        qte, qte_action = _qte()
        rows = BattleLinkoCoattackInferenceService.infer(
            (qte,),
            (qte_action,),
            time_stop_projection=_time_stops(),
            animation_candidates=(_e_animation(),),
            type6_evidence=(BattleLinkoType6Evidence(
                event_id="type6:only",
                relative_time_us=4_000_000,
                target_id="target-1",
                confidence="高",
            ),),
        )
        self.assertEqual((), rows)

    def test_incomplete_e_does_not_claim_a_later_qte(self):
        e_hits, _e_action = _linko_e()
        incomplete = e_hits[:3]
        incomplete_action = _action("linko-e:short", "E", 1072, incomplete)
        qte, qte_action = _qte()

        self.assertEqual(
            (),
            BattleLinkoCoattackInferenceService.infer(
                (*incomplete, qte),
                (incomplete_action, qte_action),
                time_stop_projection=_time_stops(),
                animation_candidates=(_e_animation(),),
            ),
        )

    def test_e_rejects_cross_target_cross_stop_and_ambiguous_qte(self):
        e_hits, e_action = _linko_e()
        other_target, other_target_action = _qte(
            sequence=10,
            time_us=2_500_000,
            target_id="target-2",
        )
        across_stop, across_stop_action = _qte(
            sequence=20,
            time_us=4_200_000,
        )
        another, another_action = _qte(
            sequence=30,
            time_us=4_600_000,
            character_id=1003,
        )

        rows = BattleLinkoCoattackInferenceService.infer(
            (*e_hits, other_target, across_stop, another),
            (e_action, other_target_action, across_stop_action, another_action),
            time_stop_projection=_time_stops(
                ((3_000_000, 4_000_000),),
                source_kind="nte_core",
                confidence="高",
            ),
            animation_candidates=(_e_animation(),),
        )
        self.assertEqual((), rows)

    def test_two_e_actions_cannot_both_claim_one_qte(self):
        first_hits, first_action = _linko_e(sequence=1, start_us=1_000_000)
        second_hits, second_action = _linko_e(sequence=20, start_us=2_000_000)
        qte, qte_action = _qte(sequence=40, time_us=4_200_000)

        rows = BattleLinkoCoattackInferenceService.infer(
            (*first_hits, *second_hits, qte),
            (first_action, second_action, qte_action),
            time_stop_projection=_time_stops(),
            animation_candidates=(_e_animation(),),
        )
        self.assertEqual((), rows)

    def test_legacy_e_selects_first_valid_qte_in_response_window(self):
        e_hits, e_action = _linko_e()
        first, first_action = _qte(sequence=20, time_us=3_500_000)
        second, second_action = _qte(
            sequence=30,
            time_us=4_200_000,
            character_id=1003,
        )

        rows = BattleLinkoCoattackInferenceService.infer(
            (*e_hits, first, second),
            (e_action, first_action, second_action),
            time_stop_projection=_time_stops(),
            animation_candidates=(_e_animation(),),
        )

        self.assertEqual(1, len(rows))
        self.assertEqual(first_action.action_id, rows[0].qte_action_id)

    def test_legacy_e_keeps_same_time_first_qte_ambiguous(self):
        e_hits, e_action = _linko_e()
        first, first_action = _qte(sequence=20, time_us=3_500_000)
        second, second_action = _qte(
            sequence=30,
            time_us=3_500_000,
            character_id=1003,
        )

        rows = BattleLinkoCoattackInferenceService.infer(
            (*e_hits, first, second),
            (e_action, first_action, second_action),
            time_stop_projection=_time_stops(),
            animation_candidates=(_e_animation(),),
        )

        self.assertEqual((), rows)

    def test_complete_e_claims_qte_before_overlapping_lte_pair(self):
        e_hits, e_action = _linko_e()
        qte, qte_action = _qte(time_us=4_200_000)
        aoe = _lte_aoe(time_us=4_280_000)

        rows = BattleLinkoCoattackInferenceService.infer(
            (*e_hits, qte, aoe),
            (e_action, qte_action),
            time_stop_projection=_time_stops(),
            animation_candidates=(_e_animation(),),
        )

        self.assertEqual({"skill"}, {row.trigger_kind for row in rows})
        self.assertEqual(e_action.action_id, rows[0].trigger_action_id)


class BattleLinkoUltraCoattackTests(unittest.TestCase):
    def test_nte_core_recorded_stop_only_assists_independent_pair_at_medium(self):
        qte, qte_action = _qte(time_us=10_000_000)
        aoe = _lte_aoe(time_us=10_080_000)
        rows = BattleLinkoCoattackInferenceService.infer(
            (qte, aoe),
            (qte_action,),
            time_stop_projection=_time_stops(
                ((9_000_000, 12_000_000),),
                source_kind="nte_core",
                confidence="高",
            ),
        )

        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual("qte_lte_pair", row.trigger_kind)
        self.assertEqual("中", row.confidence)
        self.assertEqual("nte_core", row.time_stop_source_kind)
        self.assertEqual("高", row.time_stop_confidence)
        self.assertEqual(80_000, row.raw_gap_us)
        self.assertEqual(0, row.active_gap_us)
        self.assertEqual((qte.event_id, aoe.event_id), row.evidence_event_ids)
        self.assertIn("配对才是触发推论主体", row.inference_basis)
        self.assertNotIn("正式", row.inference_basis)

    def test_nte_core_recorded_stop_without_lte_pair_does_not_prove_ultra(self):
        qte, qte_action = _qte(time_us=10_000_000)
        self.assertEqual(
            (),
            BattleLinkoCoattackInferenceService.infer(
                (qte,),
                (qte_action,),
                time_stop_projection=_time_stops(
                    ((9_000_000, 12_000_000),),
                    source_kind="nte_core",
                    confidence="高",
                ),
            ),
        )

    def test_inferred_q_action_needs_pair_and_remains_low(self):
        qte, qte_action = _qte(time_us=10_000_000)
        aoe = _lte_aoe(time_us=10_080_000)
        projection = _time_stops(
            ((9_000_000, 12_000_000),),
            source_kind="inferred_q_action",
            confidence="低",
        )

        paired = BattleLinkoCoattackInferenceService.infer(
            (qte, aoe),
            (qte_action,),
            time_stop_projection=projection,
        )
        unpaired = BattleLinkoCoattackInferenceService.infer(
            (qte,),
            (qte_action,),
            time_stop_projection=projection,
        )

        self.assertEqual(1, len(paired))
        self.assertEqual("低", paired[0].confidence)
        self.assertIn("不能单独证明触发", paired[0].inference_basis)
        self.assertEqual((), unpaired)

    def test_pair_without_usable_time_stop_stays_low_and_does_not_prove_q(self):
        qte, qte_action = _qte(time_us=10_000_000)
        aoe = _lte_aoe(time_us=10_080_000)
        rows = BattleLinkoCoattackInferenceService.infer(
            (qte, aoe),
            (qte_action,),
            time_stop_projection=_time_stops(
                ((9_000_000, 12_000_000),),
                source_kind="legacy_guess",
                confidence="高",
            ),
        )
        self.assertEqual(1, len(rows))
        self.assertEqual("低", rows[0].confidence)
        self.assertEqual("qte_lte_pair", rows[0].trigger_kind)
        self.assertEqual("none", rows[0].time_stop_source_kind)
        self.assertIn("不据此证明由灵可 Q 或 E 触发", rows[0].inference_basis)

    def test_two_qte_actions_cannot_compete_for_one_lte_aoe(self):
        first, first_action = _qte(sequence=10, time_us=10_000_000)
        second, second_action = _qte(
            sequence=12,
            time_us=10_020_000,
            character_id=1003,
        )
        aoe = _lte_aoe(sequence=13, time_us=10_080_000)

        rows = BattleLinkoCoattackInferenceService.infer(
            (first, second, aoe),
            (first_action, second_action),
            time_stop_projection=_time_stops(),
        )

        self.assertEqual((), rows)

    def test_lte_pair_is_claimed_before_overlapping_e_candidate(self):
        e_hits, e_action = _linko_e()
        qte, qte_action = _qte(time_us=4_200_000)
        aoe = _lte_aoe(time_us=4_280_000)
        rows = BattleLinkoCoattackInferenceService.infer(
            (*e_hits, qte, aoe),
            (e_action, qte_action),
            time_stop_projection=_time_stops(
                ((4_000_000, 5_000_000),),
                source_kind="nte_core",
                confidence="高",
            ),
            animation_candidates=(_e_animation(),),
        )
        self.assertEqual(1, len(rows))
        self.assertEqual("qte_lte_pair", rows[0].trigger_kind)


class BattleLinkoCoattackFormulaIdentityTests(unittest.TestCase):
    def test_actor_definition_element_and_linko_panel_remain_separate(self):
        e_hits, e_action = _linko_e()
        qte, qte_action = _qte(
            time_us=4_200_000,
            damage_attribute="incantation",
        )
        row = BattleLinkoCoattackInferenceService.infer(
            (*e_hits, qte),
            (e_action, qte_action),
            time_stop_projection=_time_stops(),
            animation_candidates=(_e_animation(),),
            character_elements={
                1036: (
                    "ECharacterElementType::"
                    "CHARACTER_ELEMENT_TYPE_INCANTATION"
                ),
            },
        )[0]

        self.assertEqual(1036, row.action_character_id)
        self.assertEqual(1036, row.definition_owner_character_id)
        self.assertEqual(1072, row.panel_character_id)
        self.assertEqual(1072, row.skill_level_character_id)
        self.assertEqual("GA_Radio072_QTE", row.skill_level_ability_id)
        self.assertEqual(1036, row.damage_attribute_source_character_id)
        self.assertEqual("incantation", row.damage_attribute)
        self.assertEqual(
            "initiator_character_static_profile",
            row.damage_attribute_source,
        )

    def test_unknown_or_xiaozhen_element_does_not_fall_back_to_linko_element(self):
        e_hits, e_action = _linko_e()
        qte, qte_action = _qte(time_us=4_200_000, damage_attribute="unknown")
        row = BattleLinkoCoattackInferenceService.infer(
            (*e_hits, qte),
            (e_action, qte_action),
            time_stop_projection=_time_stops(),
            animation_candidates=(_e_animation(),),
        )[0]

        self.assertEqual("unknown", row.damage_attribute)
        self.assertEqual(
            "initiator_character_static_profile",
            row.damage_attribute_source,
        )

    def test_ordinary_qte_outside_e_and_q_context_is_not_relabelled(self):
        qte, qte_action = _qte(time_us=20_000_000)

        rows = BattleLinkoCoattackInferenceService.infer(
            (qte,),
            (qte_action,),
            time_stop_projection=_time_stops(),
            character_elements={1036: "CHARACTER_ELEMENT_TYPE_INCANTATION"},
        )

        self.assertEqual((), rows)


if __name__ == "__main__":
    unittest.main()
