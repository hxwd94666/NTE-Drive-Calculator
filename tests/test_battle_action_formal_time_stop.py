# 验证 nte-core 正式时停覆盖 Q 动作，不再受静态动画 GE 分支名否决。
from __future__ import annotations

import unittest

from src.domain.battle_report import BattleAnalysisHit
from src.services.battle_action_inference_service import (
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
        self.assertNotIn("静态动画", actions[0].inference_basis)


if __name__ == "__main__":
    unittest.main()
