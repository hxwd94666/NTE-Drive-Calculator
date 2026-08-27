# 验证推算输入轴只从可解释的角色主动出伤窗口生成。
from __future__ import annotations

import unittest

from src.domain.battle_report import BattleAnalysisHit
from src.services.battle_action_inference_service import (
    ACTION_INFERENCE_MODEL_VERSION,
    BattleActionAnimationCandidate,
    BattleActionInferenceService,
)


def _hit(
    sequence: int,
    time_us: int,
    *,
    ability_id: str = "GA_Test_Melee",
    effect_id: str = "GE_Player_Test_Melee1_Damage",
    attack_type: str = "普攻",
    classification: str = "direct",
    follow_up: bool = False,
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=f"{sequence}:primary",
        sequence=sequence,
        relative_time_us=time_us,
        character_id=1001,
        character_name="测试角色",
        skill_name="普通攻击：测试连段",
        damage_name="测试连段",
        damage_component="skill",
        attack_type=attack_type,
        damage_attribute="CHAOS",
        target_id="target-1",
        target_name="测试目标",
        damage=100.0,
        direction="outgoing",
        is_follow_up=follow_up,
        classification=classification,
        ability_id=ability_id,
        gameplay_effect_id=effect_id,
    )


class BattleActionInferenceServiceTests(unittest.TestCase):
    def test_groups_one_keyboard_press_phases_and_keeps_evidence_references(self) -> None:
        actions = BattleActionInferenceService.infer(
            (
                _hit(
                    1,
                    1_000_000,
                    ability_id="GA_Test_Skill",
                    effect_id="GE_Player_Test_Skill1_Damage",
                    attack_type="E技能",
                ),
                _hit(
                    2,
                    1_250_000,
                    ability_id="GA_Test_Skill",
                    effect_id="GE_Player_Test_Skill2_Damage",
                    attack_type="E技能",
                ),
            )
        )

        self.assertEqual("battle-action-window-v12", ACTION_INFERENCE_MODEL_VERSION)
        self.assertEqual(1, len(actions))
        self.assertEqual("E", actions[0].input_kind)
        self.assertEqual("E1 E2", actions[0].input_sequence)
        self.assertEqual(("1:primary", "2:primary"), actions[0].evidence_event_ids)
        self.assertEqual(1_000_000, actions[0].start_us)
        self.assertEqual(1_250_001, actions[0].end_us)
        self.assertEqual("中", actions[0].identity_confidence)
        self.assertEqual("低", actions[0].timing_confidence)

    def test_normal_attack_combo_phases_are_separate_mouse_taps(self) -> None:
        actions = BattleActionInferenceService.infer(
            (
                _hit(1, 1_000_000),
                _hit(
                    2,
                    1_250_000,
                    effect_id="GE_Player_Test_Melee2_Damage",
                ),
            )
        )

        self.assertEqual(2, len(actions))
        self.assertEqual(("A1", "A2"), tuple(row.input_sequence for row in actions))

    def test_splits_after_gap_and_excludes_secondary_damage(self) -> None:
        actions = BattleActionInferenceService.infer(
            (
                _hit(1, 1_000_000),
                _hit(2, 2_100_000),
                _hit(
                    3,
                    2_200_000,
                    effect_id="GE_Player_Test_DotDamage",
                ),
                _hit(
                    4,
                    2_300_000,
                    effect_id="Buff_Reaction_4_new",
                    attack_type="黯星",
                    classification="reaction",
                ),
                _hit(5, 2_400_000, follow_up=True),
            )
        )

        self.assertEqual(2, len(actions))
        self.assertEqual(("1:primary",), actions[0].evidence_event_ids)
        self.assertEqual(("2:primary",), actions[1].evidence_event_ids)

    def test_evade_and_parry_counters_share_the_normal_attack_action_kind(self) -> None:
        actions = BattleActionInferenceService.infer(
            (
                _hit(
                    1,
                    1_000_000,
                    ability_id="GA_Test_ExtremEvadeAtk",
                    effect_id="GE_Player_Test_PerfectEvadeAttack_Damage",
                    attack_type="闪避反击",
                ),
                _hit(
                    2,
                    2_000_000,
                    ability_id="",
                    effect_id="GE_Parry_Damage",
                    attack_type="格挡反击",
                ),
            )
        )

        self.assertEqual("battle-action-window-v12", ACTION_INFERENCE_MODEL_VERSION)
        self.assertEqual(("A", "A"), tuple(action.input_kind for action in actions))

    def test_simultaneous_melee_and_parry_damage_are_one_a_operation(self) -> None:
        actions = BattleActionInferenceService.infer(
            (
                _hit(1, 1_000_000),
                _hit(
                    2,
                    1_000_000,
                    ability_id="",
                    effect_id="GE_Parry_Damage",
                    attack_type="格挡反击",
                ),
                _hit(
                    3,
                    1_000_000,
                    ability_id="",
                    effect_id="GE_Parry_Damage",
                    attack_type="格挡反击",
                ),
            )
        )

        self.assertEqual(1, len(actions))
        self.assertEqual("A", actions[0].input_kind)
        self.assertEqual(
            ("1:primary", "2:primary", "3:primary"),
            actions[0].evidence_event_ids,
        )

    def test_formal_appear_ability_is_a_g_action_even_when_hit_looks_like_melee(self) -> None:
        actions = BattleActionInferenceService.infer((
            _hit(
                1,
                1_000_000,
                ability_id="GA_Cang_Appear",
                effect_id="GE_Player_Cang_MeleeJump_Damage",
                attack_type="普攻",
            ),
        ))

        self.assertEqual(1, len(actions))
        self.assertEqual("G", actions[0].input_kind)

    def test_qte_child_abilities_merge_into_one_main_qte_action(self) -> None:
        actions = BattleActionInferenceService.infer((
            _hit(
                1,
                1_000_000,
                ability_id="GA_Radio072_QTE",
                effect_id="GE_Player_Radio072_QTE_Damage",
                attack_type="环合·援护技",
            ),
            _hit(
                2,
                1_200_000,
                ability_id="GA_Radio072_QTE_BackToLTE",
                effect_id="GE_Player_Radio072_LTE_Damage",
                attack_type="环合·援护技",
            ),
        ))

        self.assertEqual(1, len(actions))
        self.assertEqual("QTE", actions[0].input_kind)
        self.assertEqual(("1:primary", "2:primary"), actions[0].evidence_event_ids)

    def test_ai_qte_damage_is_not_a_player_qte_action(self) -> None:
        actions = BattleActionInferenceService.infer((
            _hit(
                1,
                1_000_000,
                ability_id="GA_Oneiroi_AI_QTE_Sheep",
                effect_id="GE_Player_Oneiroi_AI_QTE_Damage",
                attack_type="环合·援护技",
            ),
        ))

        self.assertEqual((), actions)

    def test_player_qte_remains_action_evidence_when_classified_as_weave(self) -> None:
        actions = BattleActionInferenceService.infer((
            _hit(
                1,
                1_000_000,
                ability_id="GA_Oneiroi_QTE",
                effect_id="GE_Player_Oneiroi_QTE_Damage",
                attack_type="环合·援护技",
                classification="weave",
            ),
        ))

        self.assertEqual(1, len(actions))
        self.assertEqual("QTE", actions[0].input_kind)

    def test_q_action_uses_the_head_and_tail_of_its_time_stop_interval(self) -> None:
        actions = BattleActionInferenceService.infer(
            (
                _hit(
                    1,
                    14_100_000,
                    ability_id="GA_Fadia_UltraSkill",
                    effect_id="GE_Player_Fadia_UltraSkill1_Damage",
                    attack_type="Q技能",
                ),
                _hit(
                    2,
                    15_283_000,
                    ability_id="GA_Fadia_UltraSkill",
                    effect_id="GE_Player_Fadia_UltraSkill3_Damage",
                    attack_type="Q技能",
                ),
            ),
            time_stop_intervals=((11_405_000, 15_818_000),),
        )

        self.assertEqual(1, len(actions))
        self.assertEqual("Q", actions[0].input_kind)
        self.assertEqual(11_405_000, actions[0].start_us)
        self.assertEqual(15_818_000, actions[0].end_us)
        self.assertIn("开始锚定到时停头", actions[0].inference_basis)

    def test_fadia_godslayer_followup_is_mouse_a_not_another_q(self) -> None:
        actions = BattleActionInferenceService.infer((
            _hit(
                1,
                16_000_000,
                ability_id="GA_Fadia_UltraSkill",
                effect_id="GE_Player_Fadia_UltraSkillMelee1_Damage",
                attack_type="Q技能",
            ),
        ))

        self.assertEqual(1, len(actions))
        self.assertEqual("A", actions[0].input_kind)
        self.assertEqual("A1", actions[0].input_sequence)
        self.assertEqual("敌神者", actions[0].action_name)

    def test_non_q_action_is_not_anchored_to_a_time_stop(self) -> None:
        actions = BattleActionInferenceService.infer(
            (_hit(1, 14_100_000),),
            time_stop_intervals=((11_405_000, 15_818_000),),
        )

        self.assertEqual(14_100_000, actions[0].start_us)
        self.assertEqual(14_100_001, actions[0].end_us)

    def test_exact_static_animation_evidence_expands_one_hit_action(self) -> None:
        actions = BattleActionInferenceService.infer(
            (
                _hit(
                    1,
                    1_000_000,
                    ability_id="GA_Test_Skill",
                    effect_id="GE_Player_Test_Skill1_Damage",
                    attack_type="E技能",
                ),
            ),
            animation_candidates=(
                BattleActionAnimationCandidate(
                    ability_id="GA_Test_Skill",
                    selector_key="Skill1",
                    montage_asset_path="/Game/Animation/Test_Skill",
                    effect_hit_offsets_us=(
                        ("GE_Player_Test_Skill1_Damage", (250_000,)),
                    ),
                    trigger_end_offsets_us=(900_000,),
                    end_event_offsets_us=(),
                    section_end_offsets_us=(1_200_000,),
                    duration_us=1_200_000,
                ),
            ),
        )

        self.assertEqual(1, len(actions))
        self.assertEqual(750_000, actions[0].start_us)
        self.assertEqual(1_650_000, actions[0].end_us)
        self.assertEqual("中", actions[0].timing_confidence)
        self.assertIn("静态动画", actions[0].inference_basis)

    def test_repeated_keyboard_phase_splits_when_one_montage_cannot_cover_both(self) -> None:
        actions = BattleActionInferenceService.infer(
            (
                _hit(
                    1,
                    1_000_000,
                    ability_id="GA_Test_Skill",
                    effect_id="GE_Player_Test_Skill1_Damage",
                    attack_type="E技能",
                ),
                _hit(
                    2,
                    1_500_000,
                    ability_id="GA_Test_Skill",
                    effect_id="GE_Player_Test_Skill1_Damage",
                    attack_type="E技能",
                ),
            ),
            animation_candidates=(
                BattleActionAnimationCandidate(
                    ability_id="GA_Test_Skill",
                    selector_key="Skill1",
                    montage_asset_path="/Game/Animation/Test_Skill",
                    effect_hit_offsets_us=(
                        ("GE_Player_Test_Skill1_Damage", (200_000,)),
                    ),
                    trigger_end_offsets_us=(400_000,),
                    end_event_offsets_us=(),
                    section_end_offsets_us=(500_000,),
                    duration_us=500_000,
                ),
            ),
        )

        self.assertEqual(2, len(actions))
        self.assertEqual(
            (("1:primary",), ("2:primary",)),
            tuple(row.evidence_event_ids for row in actions),
        )

    def test_static_hold_sequence_keeps_non_monotonic_internal_skill_numbers(self) -> None:
        hits = (
            _hit(
                1,
                1_000_000,
                ability_id="GA_Test_Skill",
                effect_id="GE_Test_Skill5_Damage",
                attack_type="E技能",
            ),
            _hit(
                2,
                1_565_000,
                ability_id="GA_Test_Skill",
                effect_id="GE_Test_Skill3_Damage",
                attack_type="E技能",
            ),
            _hit(
                3,
                2_311_000,
                ability_id="GA_Test_Skill",
                effect_id="GE_Test_Skill4_Damage",
                attack_type="E技能",
            ),
        )
        candidate = BattleActionAnimationCandidate(
            ability_id="GA_Test_Skill",
            selector_key="Skill2",
            montage_asset_path="/Game/Animation/Skill2",
            effect_hit_offsets_us=(
                ("GE_Test_Skill5_Damage", (123_000,)),
                ("GE_Test_Skill3_Damage", (688_000,)),
                ("GE_Test_Skill4_Damage", (1_434_000,)),
            ),
            trigger_end_offsets_us=(2_500_000,),
            end_event_offsets_us=(),
            section_end_offsets_us=(5_500_000,),
            duration_us=5_500_000,
            hold_damage_mode="after_hold",
            hold_prelude_us=200_000,
        )

        actions = BattleActionInferenceService.infer(
            hits,
            animation_candidates=(candidate,),
        )

        self.assertEqual(1, len(actions))
        self.assertEqual("E5 E3 E4", actions[0].input_sequence)
        self.assertEqual("after_hold", actions[0].hold_damage_mode)

    def test_hold_selector_marks_hold_without_exposing_a_branch_label(self) -> None:
        actions = BattleActionInferenceService.infer(
            (
                _hit(
                    1,
                    1_000_000,
                    ability_id="GA_Test_Skill",
                    effect_id="GE_Player_Test_Skill3_Damage",
                    attack_type="E技能",
                ),
            ),
            animation_candidates=(
                BattleActionAnimationCandidate(
                    ability_id="GA_Test_Skill",
                    selector_key="Skill3Hold",
                    montage_asset_path="/Game/Animation/Test_Skill_Hold",
                    effect_hit_offsets_us=(
                        ("GE_Player_Test_Skill3_Damage", (300_000,)),
                    ),
                    trigger_end_offsets_us=(1_300_000,),
                    end_event_offsets_us=(),
                    section_end_offsets_us=(1_400_000,),
                    duration_us=1_400_000,
                    hold_damage_mode="during_hold",
                ),
            ),
        )

        self.assertEqual("hold", actions[0].input_gesture)
        self.assertEqual("during_hold", actions[0].hold_damage_mode)
        self.assertLessEqual(actions[0].input_start_us, actions[0].start_us)
        self.assertGreater(actions[0].input_end_us, 1_000_000)
        self.assertEqual("E3", actions[0].input_sequence)
        self.assertNotIn("Hold", actions[0].input_sequence)

    def test_release_damage_starts_after_the_hold_input_ends(self) -> None:
        actions = BattleActionInferenceService.infer(
            (
                _hit(
                    1,
                    4_300_000,
                    ability_id="GA_Test_Melee",
                    effect_id="GE_Test_Branch1_Damage",
                ),
            ),
            animation_candidates=(
                BattleActionAnimationCandidate(
                    ability_id="GA_Test_Melee",
                    selector_key="Branch1",
                    montage_asset_path="/Game/Animation/Branch1",
                    effect_hit_offsets_us=(
                        ("GE_Test_Branch1_Damage", (300_000,)),
                    ),
                    trigger_end_offsets_us=(800_000,),
                    end_event_offsets_us=(),
                    section_end_offsets_us=(1_000_000,),
                    duration_us=1_000_000,
                    hold_damage_mode="after_hold",
                    hold_prelude_us=3_000_000,
                ),
            ),
        )

        action = actions[0]
        self.assertEqual("hold", action.input_gesture)
        self.assertEqual("after_hold", action.hold_damage_mode)
        self.assertEqual(1_000_000, action.input_start_us)
        self.assertEqual(4_000_000, action.input_end_us)
        self.assertGreater(4_300_000, action.input_end_us)

    def test_next_action_truncates_previous_static_animation_window(self) -> None:
        actions = BattleActionInferenceService.infer(
            (
                _hit(1, 1_000_000),
                _hit(
                    2,
                    1_800_000,
                    ability_id="GA_Test_Skill",
                    effect_id="GE_Player_Test_Skill1_Damage",
                    attack_type="E技能",
                ),
            ),
            animation_candidates=(
                BattleActionAnimationCandidate(
                    ability_id="GA_Test_Melee",
                    selector_key="Melee1",
                    montage_asset_path="/Game/Animation/Test_Melee",
                    effect_hit_offsets_us=(
                        ("GE_Player_Test_Melee1_Damage", (200_000,)),
                    ),
                    trigger_end_offsets_us=(2_500_000,),
                    end_event_offsets_us=(),
                    section_end_offsets_us=(3_000_000,),
                    duration_us=3_000_000,
                ),
                BattleActionAnimationCandidate(
                    ability_id="GA_Test_Skill",
                    selector_key="Skill1",
                    montage_asset_path="/Game/Animation/Test_Skill",
                    effect_hit_offsets_us=(
                        ("GE_Player_Test_Skill1_Damage", (200_000,)),
                    ),
                    trigger_end_offsets_us=(900_000,),
                    end_event_offsets_us=(),
                    section_end_offsets_us=(1_000_000,),
                    duration_us=1_000_000,
                ),
            ),
        )

        self.assertEqual(2, len(actions))
        self.assertEqual(1_600_000, actions[0].end_us)
        self.assertEqual(1_600_000, actions[1].start_us)
        self.assertIn("后续动作开始", actions[0].inference_basis)

    def test_q_time_stop_head_has_priority_over_static_animation_start(self) -> None:
        actions = BattleActionInferenceService.infer(
            (
                _hit(
                    1,
                    14_100_000,
                    ability_id="GA_Fadia_UltraSkill",
                    effect_id="GE_Player_Fadia_UltraSkill1_Damage",
                    attack_type="Q技能",
                ),
            ),
            time_stop_intervals=((11_405_000, 15_818_000),),
            animation_candidates=(
                BattleActionAnimationCandidate(
                    ability_id="GA_Fadia_UltraSkill",
                    selector_key="UltraSkill",
                    montage_asset_path="/Game/Animation/Fadia_UltraSkill",
                    effect_hit_offsets_us=(
                        ("GE_Player_Fadia_UltraSkill1_Damage", (2_700_000,)),
                    ),
                    trigger_end_offsets_us=(5_000_000,),
                    end_event_offsets_us=(),
                    section_end_offsets_us=(8_000_000,),
                    duration_us=8_000_000,
                ),
            ),
        )

        self.assertEqual(11_405_000, actions[0].start_us)
        self.assertEqual(16_400_000, actions[0].end_us)

    def test_ambiguous_static_windows_keep_original_hit_window(self) -> None:
        shared = {
            "ability_id": "GA_Test_Skill",
            "effect_hit_offsets_us": (
                ("GE_Player_Test_Skill1_Damage", (200_000,)),
            ),
            "end_event_offsets_us": (),
            "section_end_offsets_us": (),
        }
        actions = BattleActionInferenceService.infer(
            (
                _hit(
                    1,
                    1_000_000,
                    ability_id="GA_Test_Skill",
                    effect_id="GE_Player_Test_Skill1_Damage",
                    attack_type="E技能",
                ),
            ),
            animation_candidates=(
                BattleActionAnimationCandidate(
                    selector_key="SkillA",
                    montage_asset_path="/Game/Animation/Test_A",
                    trigger_end_offsets_us=(800_000,),
                    duration_us=1_000_000,
                    **shared,
                ),
                BattleActionAnimationCandidate(
                    selector_key="SkillB",
                    montage_asset_path="/Game/Animation/Test_B",
                    trigger_end_offsets_us=(1_400_000,),
                    duration_us=1_600_000,
                    **shared,
                ),
            ),
        )

        self.assertEqual(1_000_000, actions[0].start_us)
        self.assertEqual(1_000_001, actions[0].end_us)
        self.assertEqual("低", actions[0].timing_confidence)

    def test_static_window_rejects_same_effects_with_wrong_notify_spacing(self) -> None:
        hits = (
            _hit(
                1,
                1_000_000,
                ability_id="GA_Test_Skill",
                effect_id="GE_Player_Test_Skill1_Damage",
                attack_type="E技能",
            ),
            _hit(
                2,
                1_400_000,
                ability_id="GA_Test_Skill",
                effect_id="GE_Player_Test_Skill2_Damage",
                attack_type="E技能",
            ),
        )
        shared = {
            "ability_id": "GA_Test_Skill",
            "end_event_offsets_us": (),
            "section_end_offsets_us": (),
        }

        actions = BattleActionInferenceService.infer(
            hits,
            animation_candidates=(
                BattleActionAnimationCandidate(
                    selector_key="SkillShort",
                    montage_asset_path="/Game/Animation/SkillShort",
                    effect_hit_offsets_us=(
                        ("GE_Player_Test_Skill1_Damage", (200_000,)),
                        ("GE_Player_Test_Skill2_Damage", (600_000,)),
                    ),
                    trigger_end_offsets_us=(1_000_000,),
                    duration_us=1_000_000,
                    **shared,
                ),
                BattleActionAnimationCandidate(
                    selector_key="SkillHold",
                    montage_asset_path="/Game/Animation/SkillHold",
                    effect_hit_offsets_us=(
                        ("GE_Player_Test_Skill1_Damage", (200_000,)),
                        ("GE_Player_Test_Skill2_Damage", (1_000_000,)),
                    ),
                    trigger_end_offsets_us=(1_400_000,),
                    duration_us=1_400_000,
                    **shared,
                ),
            ),
        )

        self.assertEqual(1, len(actions))
        self.assertEqual(800_000, actions[0].start_us)
        self.assertEqual(1_800_000, actions[0].end_us)
        self.assertEqual("中", actions[0].timing_confidence)


if __name__ == "__main__":
    unittest.main()
