# 验证 nte-core 记录时停只锚定 Q 展示边界，不受静态动画 GE 分支名否决。
from __future__ import annotations

import unittest
from dataclasses import replace

from src.domain.battle_report.timeline.events import BattleAnalysisHit
from src.services.battle_report.actions.inference import (
    BattleActionAnimationCandidate,
    BattleActionInferenceService,
)


def _q_hit(sequence: int, time_us: int, effect_id: str) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=f"{sequence}:primary",
        sequence=sequence,
        relative_time_us=time_us,
        character_id=1036,
        character_name="残虹",
        skill_name="极轨终结",
        damage_name="极轨终结",
        damage_component="skill",
        attack_type="Q技能",
        damage_attribute="incantation",
        target_id="target",
        target_name="目标",
        damage=100.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        ability_id="GA_Zankou_UltraSkill",
        gameplay_effect_id=effect_id,
    )


class BattleActionFormalTimeStopTests(unittest.TestCase):
    def test_legacy_zankou_magic_and_force_halves_form_one_q_action(self) -> None:
        q_hits = (
            _q_hit(1, 10_000_000, "GE_Player_Zankou_MagicUltraSkill1_Damage"),
            _q_hit(2, 11_000_000, "GE_Player_Zankou_MagicUltraSkill2_Damage"),
            _q_hit(3, 17_000_000, "GE_Player_Zankou_ForceUltraSkill1_Damage"),
            _q_hit(4, 17_600_000, "GE_Player_Zankou_ForceUltraSkill2_Damage"),
        )
        interleaved = replace(
            _q_hit(5, 12_000_000, "GE_Player_Zankou_Melee1_Damage"),
            skill_name="普攻",
            attack_type="普攻",
            ability_id="GA_Zankou_Melee",
        )

        actions = BattleActionInferenceService.infer((*q_hits, interleaved))
        q_actions = tuple(action for action in actions if action.input_kind == "Q")

        self.assertEqual(1, len(q_actions))
        self.assertEqual(
            tuple(hit.event_id for hit in q_hits),
            q_actions[0].evidence_event_ids,
        )

    def test_recorded_pauses_constrain_both_contiguous_and_interleaved_q_halves(self):
        magic = _q_hit(1, 6_000_000, "GE_Zankou_MagicUltraSkill1")
        force = _q_hit(2, 10_000_000, "GE_Zankou_ForceUltraSkill1")
        ordinary = replace(_q_hit(3, 8_000_000, "GE_Zankou_Melee1"),
                           attack_type="普攻", ability_id="GA_Zankou_Melee")
        # 相邻输入与被普攻打断的后归组合并必须遵守同一记录门槛。
        for middle in ((), (ordinary,)):
            for pauses, groups in (((), 1), (((None, 9_000_000),), 1),
                                   (((5_000_000, 11_000_000),), 1),
                                   (((1_000_000, 2_000_000),), 2),
                                   (((5_000_000, 7_000_000), (9_000_000, 11_000_000)), 2)):
                with self.subTest(interleaved=bool(middle), pauses=pauses):
                    actions = BattleActionInferenceService.infer(
                        (magic, *middle, force), time_stop_intervals=pauses,
                    )
                    q = tuple(a for a in actions if a.input_kind == "Q")
                    self.assertEqual(groups, len(q))
                    self.assertEqual((magic.event_id, force.event_id),
                                     tuple(e for a in q for e in a.evidence_event_ids))
                    self.assertEqual(200.0, sum(a.damage for a in q))

        # 首段从暂停外延续到暂停内时，不能仅凭末两击在暂停内跳过两形态门槛。
        crossing = (
            _q_hit(1, 900_000, "GE_Zankou_MagicUltraSkill1"),
            _q_hit(2, 1_100_000, "GE_Zankou_MagicUltraSkill2"),
            _q_hit(3, 1_200_000, "GE_Zankou_ForceUltraSkill1"),
        )
        for force_us in (1_200_000, 10_000_000):
            # 超出旧八秒邻接窗也须先拒绝跨暂停，不能落入通用 Q 快捷分支。
            variant = (*crossing[:2], replace(crossing[2], relative_time_us=force_us))
            q = BattleActionInferenceService.infer(variant, time_stop_intervals=((1_000_000, 11_000_000),))
            self.assertEqual(((crossing[0].event_id, crossing[1].event_id), (crossing[2].event_id,)),
                             tuple(action.evidence_event_ids for action in q))
            self.assertEqual(300.0, sum(action.damage for action in q))

    def test_formal_time_stop_keeps_q_when_animation_uses_base_ge_ids(self) -> None:
        hits = (
            _q_hit(1, 10_020_699, "GE_Player_Zankou_MagicUltraSkill1_Damage"),
            _q_hit(2, 11_244_461, "GE_Player_Zankou_MagicUltraSkill2_Damage"),
            _q_hit(3, 15_894_679, "GE_Player_Zankou_ForceUltraSkill1_Damage"),
            _q_hit(4, 16_510_519, "GE_Player_Zankou_ForceUltraSkill2_Damage"),
        )
        actions = BattleActionInferenceService.infer(
            hits,
            time_stop_intervals=((7_044_097, 17_561_134),),
            animation_candidates=(BattleActionAnimationCandidate(
                ability_id="GA_Zankou_UltraSkill",
                selector_key="UltraSkill",
                montage_asset_path="/Game/Animation/Zankou_UltraSkill",
                effect_hit_offsets_us=(
                    ("GE_Player_Zankou_UltraSkill1_Damage", (1_000_000,)),
                    ("GE_Player_Zankou_UltraSkill2_Damage", (2_000_000,)),
                ),
                trigger_end_offsets_us=(20_000_000,),
                end_event_offsets_us=(),
                section_end_offsets_us=(20_000_000,),
                duration_us=20_000_000,
            ),),
        )

        self.assertEqual(1, len(actions))
        self.assertEqual("Q", actions[0].input_kind)
        self.assertEqual(7_044_097, actions[0].start_us)
        self.assertEqual(17_561_134, actions[0].end_us)
        self.assertEqual(tuple(hit.event_id for hit in hits), actions[0].evidence_event_ids)


if __name__ == "__main__":
    unittest.main()
