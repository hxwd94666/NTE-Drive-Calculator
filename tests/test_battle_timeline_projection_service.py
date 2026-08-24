# 验证统一时间轴不混并技能，并显式区分推算输入与正式逐击。
from __future__ import annotations

import unittest
from dataclasses import replace

from src.domain.battle_report import BattleAnalysisHit, BattleInferredAction
from src.services.battle_timeline_projection_service import (
    BattleTimelineProjectionService,
)


def _hit(
    sequence: int,
    time_us: int,
    *,
    ability_id: str,
    skill_name: str,
    damage_name: str = "伤害",
    classification: str = "direct",
    attack_type: str = "普攻",
    gameplay_effect_id: str | None = None,
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=f"{sequence}:primary",
        sequence=sequence,
        relative_time_us=time_us,
        character_id=1001,
        character_name="测试角色",
        skill_name=skill_name,
        damage_name=damage_name,
        damage_component="skill",
        attack_type=attack_type,
        damage_attribute="CHAOS",
        target_id="target-1",
        target_name="测试目标",
        damage=float(sequence * 100),
        direction="outgoing",
        is_follow_up=False,
        classification=classification,
        ability_id=ability_id,
        gameplay_effect_id=gameplay_effect_id or f"GE_Test_{sequence}",
    )


def _action(*, kind: str, ordinal: int) -> BattleInferredAction:
    return BattleInferredAction(
        action_id=f"action:{ordinal}",
        character_id=1001,
        character_name="测试角色",
        action_name="测试技能",
        input_kind=kind,
        input_sequence="A1 A2" if kind == "A" else kind,
        start_us=ordinal * 1_000_000,
        end_us=ordinal * 1_000_000 + 1,
        hits=1,
        damage=100.0,
        identity_confidence="中",
        timing_confidence="低",
        inference_basis="正式逐击窗口",
        evidence_event_ids=(f"{ordinal}:primary",),
        gameplay_effect_ids=(f"GE_Test_{ordinal}",),
    )


class BattleTimelineProjectionServiceTests(unittest.TestCase):
    def test_same_ability_merges_but_a_and_e_never_share_a_bar(self) -> None:
        groups = BattleTimelineProjectionService.group_damage_hits(
            (
                _hit(
                    1,
                    1_000_000,
                    ability_id="GA_Test_Melee",
                    skill_name="普通攻击",
                    damage_name="普攻伤害",
                ),
                _hit(
                    2,
                    1_300_000,
                    ability_id="GA_Test_Melee",
                    skill_name="普通攻击",
                    damage_name="普攻伤害",
                ),
                _hit(
                    3,
                    1_350_000,
                    ability_id="GA_Test_Skill",
                    skill_name="E技能",
                    damage_name="E技能伤害",
                ),
            )
        )

        self.assertEqual(2, len(groups))
        self.assertEqual(
            {"普攻伤害", "E技能伤害"},
            {group.damage_name for group in groups},
        )
        melee = next(group for group in groups if group.ability_id == "GA_Test_Melee")
        skill = next(group for group in groups if group.ability_id == "GA_Test_Skill")
        self.assertEqual(("1:primary", "2:primary"), melee.evidence_event_ids)
        self.assertEqual(300.0, melee.damage)
        self.assertEqual(("3:primary",), skill.evidence_event_ids)

    def test_returning_to_melee_after_skill_starts_a_new_damage_bar(self) -> None:
        groups = BattleTimelineProjectionService.group_damage_hits(
            (
                _hit(1, 1_000_000, ability_id="GA_Test_Melee", skill_name="普通攻击"),
                _hit(2, 1_200_000, ability_id="GA_Test_Skill", skill_name="E技能"),
                _hit(3, 1_300_000, ability_id="GA_Test_Melee", skill_name="普通攻击"),
            )
        )

        self.assertEqual(3, len(groups))
        self.assertEqual(
            (("1:primary",), ("3:primary",)),
            tuple(
                group.evidence_event_ids
                for group in groups
                if group.ability_id == "GA_Test_Melee"
            ),
        )

    def test_named_reaction_and_special_damage_use_distinct_lanes(self) -> None:
        groups = BattleTimelineProjectionService.group_damage_hits(
            (
                _hit(
                    1,
                    1_000_000,
                    ability_id="GA_Test_Reaction",
                    skill_name="黯星",
                    damage_name="黯星",
                    classification="reaction",
                ),
                _hit(
                    2,
                    1_100_000,
                    ability_id="GA_Test_Nightmare",
                    skill_name="噩梦",
                    damage_name="噩梦",
                    classification="special",
                ),
            )
        )

        self.assertEqual(
            {"reaction_nova", "special_nightmare"},
            {row.channel_key for row in groups},
        )

    def test_topple_damage_does_not_use_the_unknown_reaction_lane(self) -> None:
        groups = BattleTimelineProjectionService.group_damage_hits(
            (
                _hit(
                    1,
                    1_000_000,
                    ability_id="",
                    skill_name="倾陷伤害",
                    damage_name="倾陷伤害",
                    classification="reaction",
                ),
            )
        )

        self.assertEqual("other_topple", groups[0].channel_key)
        self.assertEqual("倾陷伤害", groups[0].channel_label)

    def test_qte_own_damage_stays_on_the_role_direct_lane(self) -> None:
        groups = BattleTimelineProjectionService.group_damage_hits(
            (
                _hit(
                    1,
                    1_000_000,
                    ability_id="GA_Lacrimosa_QTE",
                    skill_name="援护技：安魂曲",
                    damage_name="援护技伤害",
                    classification="direct",
                    attack_type="环合·浊燃",
                    gameplay_effect_id="GE_Player_Lacrimosa_QTE1_Damage",
                ),
            )
        )

        self.assertEqual("direct", groups[0].channel_key)
        self.assertEqual("直伤", groups[0].channel_label)

    def test_explicit_reaction_effect_is_not_reclassified_as_qte_direct_damage(self) -> None:
        groups = BattleTimelineProjectionService.group_damage_hits(
            (
                _hit(
                    1,
                    1_000_000,
                    ability_id="GA_Lacrimosa_QTE",
                    skill_name="浊燃",
                    damage_name="浊燃",
                    classification="reaction",
                    attack_type="环合·浊燃",
                    gameplay_effect_id="Buff_Reaction_5_new_1036",
                ),
            )
        )

        self.assertEqual("reaction_scorch", groups[0].channel_key)

    def test_zankou_dot_damage_uses_two_special_lanes(self) -> None:
        erosion = _hit(
            1,
            1_000_000,
            ability_id="GA_Zankou_Melee",
            skill_name="普通攻击：燎原",
            damage_name="蚀心",
        )
        venom = _hit(
            2,
            1_100_000,
            ability_id="GA_Zankou_UltraSkill",
            skill_name="极轨终结：燎原",
            damage_name="鸩火",
        )

        groups = BattleTimelineProjectionService.group_damage_hits((erosion, venom))

        self.assertEqual(
            {"special_zankou_erosion", "special_zankou_venom"},
            {row.channel_key for row in groups},
        )
        labels = {group.channel_key: group.damage_name for group in groups}
        sources = {group.channel_key: group.source_skill_name for group in groups}
        self.assertEqual("蚀心", labels["special_zankou_erosion"])
        self.assertEqual("鸩火", labels["special_zankou_venom"])
        self.assertEqual("普通攻击：燎原", sources["special_zankou_erosion"])
        self.assertEqual("极轨终结：燎原", sources["special_zankou_venom"])

    def test_lacrimosa_stolen_skills_do_not_merge_across_damage_effects(self) -> None:
        groups = BattleTimelineProjectionService.group_damage_hits(
            (
                replace(
                    _hit(
                        1,
                        1_000_000,
                        ability_id="GA_Lacrimosa_Skill",
                        skill_name="习得技巧",
                        damage_name="习得技巧 A",
                    ),
                    gameplay_effect_id="GE_boss_018_act019_Steal_Dmg_BP",
                ),
                replace(
                    _hit(
                        2,
                        1_100_000,
                        ability_id="GA_Lacrimosa_Skill",
                        skill_name="习得技巧",
                        damage_name="习得技巧 B",
                    ),
                    gameplay_effect_id="GE_boss_019_Steal_Dmg_BP",
                ),
            )
        )

        self.assertEqual(2, len(groups))
        self.assertEqual(
            {"习得技巧 A", "习得技巧 B"},
            {group.damage_name for group in groups},
        )

    def test_unknown_damage_group_uses_original_ability_name(self) -> None:
        groups = BattleTimelineProjectionService.group_damage_hits(
            (
                _hit(
                    1,
                    1_000_000,
                    ability_id="GA_Test_Skill",
                    skill_name="未知技能",
                    damage_name="未知伤害",
                    attack_type="E技能",
                ),
            )
        )

        self.assertEqual("GA_Test_Skill", groups[0].damage_name)

    def test_input_projection_marks_mouse_and_character_switch_without_fake_key(self) -> None:
        inferred_e = replace(
            _action(kind="E", ordinal=3),
            input_sequence="E1 E3",
            end_us=5_000_000,
        )
        inputs = BattleTimelineProjectionService.infer_inputs(
            (
                _action(kind="A", ordinal=1),
                _action(kind="QTE", ordinal=2),
                inferred_e,
            )
        )

        self.assertEqual("mouse", inputs[0].device_kind)
        self.assertEqual("A", inputs[0].display_text)
        self.assertEqual("keyboard", inputs[1].device_kind)
        self.assertTrue(inputs[1].is_character_switch)
        self.assertEqual("", inputs[1].display_text)
        self.assertNotIn("QTE", inputs[1].display_text)
        self.assertEqual("E", inputs[2].display_text)
        self.assertNotIn("E1", inputs[2].display_text)
        self.assertEqual(inputs[2].start_us + 1, inputs[2].end_us)

    def test_confirmed_hold_input_uses_the_extended_action_window(self) -> None:
        action = replace(
            _action(kind="E", ordinal=1),
            input_gesture="hold",
            input_start_us=1_000_000,
            input_end_us=2_400_000,
            hold_damage_mode="during_hold",
            end_us=3_000_000,
        )

        inferred = BattleTimelineProjectionService.infer_inputs((action,))[0]

        self.assertEqual("E", inferred.display_text)
        self.assertEqual(action.input_start_us, inferred.start_us)
        self.assertEqual(action.input_end_us, inferred.end_us)
        self.assertEqual("during_hold", inferred.hold_damage_mode)

    def test_confirmed_hold_normal_attack_is_named_z(self) -> None:
        action = replace(
            _action(kind="A", ordinal=1),
            input_gesture="hold",
            end_us=3_000_000,
        )

        inferred = BattleTimelineProjectionService.infer_inputs((action,))[0]

        self.assertEqual("Z", inferred.display_text)


if __name__ == "__main__":
    unittest.main()
