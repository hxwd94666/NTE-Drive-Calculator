# 验证妖刀叠层按逐击正向重放，并在时停期间冻结计时。
from __future__ import annotations

from types import SimpleNamespace
import unittest

from src.domain.battle_report import BattleAnalysisHit
from src.services.battle_buff_inference_service import (
    BattleBuffInferenceService,
    BattleStaticBuffRule,
)
from src.services.battle_damage_stack_buff_service import (
    demon_blade_damage_stack_rules,
)


def _rule() -> BattleStaticBuffRule:
    selected = SimpleNamespace(
        effect_definition_id="fork_star:upgradestar_pack_fork_DemonBlade:1",
        character_id=1036,
        character_name="残虹",
        definition={
            "parameters": [
                {"name_id": "buff_DemonBlade_CritDamageUp", "value": 0.09},
                {"name_id": "buff_DemonBlade_CD", "value": 15.0},
            ],
        },
    )
    return demon_blade_damage_stack_rules(
        selected,
        BattleStaticBuffRule,
    )[0]


def _hit(
    sequence: int,
    time_us: int,
    *,
    effect: str = "GE_Player_Zankou_Melee1_Damage",
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=f"{sequence}:primary",
        sequence=sequence,
        relative_time_us=time_us,
        character_id=1036,
        character_name="残虹",
        skill_name="普通攻击",
        damage_name="燎原",
        damage_component="unknown",
        attack_type="普攻",
        damage_attribute="unknown",
        target_id="target",
        target_name="目标",
        damage=1000.0,
        direction="outgoing",
        is_follow_up=False,
        classification="reaction" if effect.startswith("Buff_Reaction") else "direct",
        gameplay_effect_id=effect,
    )


class BattleDamageStackBuffServiceTests(unittest.TestCase):
    def test_current_hit_uses_pre_hit_stack_and_cooldown_uses_active_clock(self):
        hits = (
            _hit(1, 1_000_000),
            _hit(2, 1_200_000),
            _hit(3, 10_200_000),
            _hit(4, 10_500_000),
        )
        intervals = BattleBuffInferenceService.infer(
            (_rule(),),
            actions=(),
            hits=hits,
            battle_end_us=30_000_000,
            time_stop_intervals=((1_100_000, 10_100_000),),
        )

        self.assertEqual(
            1,
            BattleBuffInferenceService.active_for_hit(intervals, hits[2])[0].stacks,
        )
        self.assertEqual(
            1,
            BattleBuffInferenceService.active_for_hit(intervals, hits[3])[0].stacks,
        )
        after_fourth = _hit(5, 10_500_001)
        self.assertEqual(
            2,
            BattleBuffInferenceService.active_for_hit(intervals, after_fourth)[0].stacks,
        )

    def test_duration_does_not_advance_during_time_stop(self):
        trigger = _hit(1, 1_000_000)
        intervals = BattleBuffInferenceService.infer(
            (_rule(),),
            actions=(),
            hits=(trigger,),
            battle_end_us=30_000_000,
            time_stop_intervals=((2_000_000, 10_000_000),),
        )

        self.assertEqual(
            1,
            BattleBuffInferenceService.active_for_hit(
                intervals,
                _hit(2, 20_000_000),
            )[0].stacks,
        )
        self.assertEqual(
            (),
            BattleBuffInferenceService.active_for_hit(
                intervals,
                _hit(3, 24_000_001),
            ),
        )

    def test_real_report_sequence_has_seven_pre_hit_stacks_at_hit_102(self):
        times = (
            (2, 1_065_759, "Buff_Reaction_5_new_1036"),
            (3, 2_050_181, "Buff_Reaction_5_new_1036"),
            (37, 8_983_006, "Buff_Reaction_5_new_1036"),
            (45, 9_966_011, "Buff_Reaction_5_new_1036"),
            (48, 10_965_834, "Buff_Reaction_5_new_1036"),
            (57, 16_383_152, "Buff_Reaction_5_new_1036"),
            (61, 16_866_401, "GE_Player_Zankou_Melee1_Damage"),
            (98, 31_843_220, "Buff_Reaction_5_new_1036"),
            (100, 32_092_936, "GE_Player_Zankou_Melee1_Damage"),
            (101, 32_309_853, "GE_Player_Zankou_Melee1_1_Damage"),
            (102, 32_577_073, "GE_Player_Zankou_Melee2_Damage"),
        )
        hits = tuple(_hit(sequence, time_us, effect=effect) for sequence, time_us, effect in times)
        intervals = BattleBuffInferenceService.infer(
            (_rule(),),
            actions=(),
            hits=hits,
            battle_end_us=34_000_000,
            time_stop_intervals=(
                (11_405_095, 15_818_446),
                (19_962_190, 23_474_210),
                (26_945_378, 30_926_002),
            ),
        )

        selected = hits[-1]
        active = BattleBuffInferenceService.active_for_hit(intervals, selected)
        self.assertEqual(1, len(active))
        self.assertEqual(7, active[0].stacks)
        self.assertEqual(0.63, active[0].modifiers[0].magnitude_value * active[0].stacks)


if __name__ == "__main__":
    unittest.main()
