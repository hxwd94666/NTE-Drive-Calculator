# 验证残虹零觉狩/惑按形态、保留时长与持续伤害口径投影。
from __future__ import annotations

import unittest

from src.domain.battle_report import BattleAnalysisHit, BattleInferredAction
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_zankou_form_buff_service import (
    BattleZankouFormBuffService,
    BattleZankouFormConfig,
)


_CONFIG = BattleZankouFormConfig(
    shou_damage_up=0.25,
    huo_dot_crit_damage_up=0.25,
    fantasy_duration_seconds=8.0,
    reality_to_fantasy_retention_seconds=8.0,
    fantasy_to_reality_retention_seconds=8.0,
)


def _build(*, awakening_level: int = 0) -> dict:
    selected = ["Effect1"] if awakening_level else []
    return {
        "characters": [{
            "character_id": 1036,
            "observed_name": "残虹",
            "awakening_level": awakening_level,
            "profile": {
                "awakening_level": awakening_level,
                "selected_awaken_effect_ids": selected,
            },
        }],
    }


def _action(
    action_id: str,
    start_us: int,
    end_us: int,
    *effects: str,
    input_kind: str = "E",
    character_id: int = 1036,
) -> BattleInferredAction:
    return BattleInferredAction(
        action_id=action_id,
        character_id=character_id,
        character_name="残虹" if character_id == 1036 else "队友",
        action_name="技能",
        input_kind=input_kind,
        input_sequence=input_kind,
        start_us=start_us,
        end_us=end_us,
        hits=1,
        damage=100.0,
        identity_confidence="中",
        timing_confidence="低",
        inference_basis="fixture",
        evidence_event_ids=(f"{action_id}:hit",),
        gameplay_effect_ids=tuple(effects),
    )


def _hit(effect_id: str, *, classification: str = "direct") -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=f"hit:{effect_id}",
        sequence=1,
        relative_time_us=3_000_000,
        character_id=1036,
        character_name="残虹",
        skill_name="测试伤害",
        damage_name="测试伤害",
        damage_component="skill",
        attack_type="普攻",
        damage_attribute="incantation",
        target_id="target",
        target_name="目标",
        damage=100.0,
        direction="outgoing",
        is_follow_up=False,
        classification=classification,
        ability_id="GA_Zankou_Melee",
        gameplay_effect_id=effect_id,
    )


class BattleZankouFormBuffServiceTests(unittest.TestCase):
    def test_zero_awakening_keeps_shou_and_starts_huo_after_feiyingshan(self) -> None:
        intervals = BattleZankouFormBuffService.infer(
            build=_build(),
            actions=(
                _action(
                    "feiyingshan",
                    1_000_000,
                    2_000_000,
                    "GE_Player_Zankou_Skill2_Damage",
                ),
            ),
            battle_end_us=25_000_000,
            config=_CONFIG,
            time_stop_intervals=((3_000_000, 5_000_000),),
        )

        shou = tuple(row for row in intervals if "shou" in row.interval_id)
        huo = tuple(row for row in intervals if "huo" in row.interval_id)
        self.assertEqual(1, len(shou))
        self.assertEqual((0, 25_000_000), (shou[0].start_us, shou[0].end_us))
        self.assertEqual(0.25, shou[0].modifiers[0].magnitude_value)
        self.assertEqual(1, len(huo))
        self.assertEqual((2_000_000, 20_000_000), (huo[0].start_us, huo[0].end_us))

    def test_huo_only_projects_crit_damage_to_continuous_damage(self) -> None:
        intervals = BattleZankouFormBuffService.infer(
            build=_build(),
            actions=(
                _action(
                    "feiyingshan",
                    1_000_000,
                    2_000_000,
                    "GE_Player_Zankou_Skill2_Damage",
                ),
            ),
            battle_end_us=20_000_000,
            config=_CONFIG,
        )

        direct = BattleBuffAttributeProjectionService.project_hit(
            _hit("GE_Player_Zankou_Melee1_Damage"),
            intervals,
        )
        dot = BattleBuffAttributeProjectionService.project_hit(
            _hit("GE_Player_Zankou_DotDamage"),
            intervals,
        )

        self.assertEqual(
            {"DamageUpGeneralBase": 0.25},
            {row.property_id: row.additive_value for row in direct.modifiers},
        )
        self.assertEqual(
            {"CritDamageBase": 0.25, "DamageUpGeneralBase": 0.25},
            {row.property_id: row.additive_value for row in dot.modifiers},
        )
        self.assertTrue(any(
            "只作用于" in reason for reason in direct.exclusion_reasons
        ))

    def test_magic_q_ends_fantasy_and_huo_retains_for_eight_active_seconds(self) -> None:
        intervals = BattleZankouFormBuffService.infer(
            build=_build(),
            actions=(
                _action(
                    "feiyingshan",
                    1_000_000,
                    2_000_000,
                    "GE_Player_Zankou_Skill2_Damage",
                ),
                _action(
                    "ultimate",
                    4_000_000,
                    6_000_000,
                    "GE_Player_Zankou_MagicUltraSkill1_Damage",
                    input_kind="Q",
                ),
            ),
            battle_end_us=20_000_000,
            config=_CONFIG,
        )

        huo = next(row for row in intervals if "huo" in row.interval_id)
        self.assertEqual((2_000_000, 14_000_000), (huo.start_us, huo.end_us))

    def test_effect_one_keeps_both_upgraded_states_for_the_whole_battle(self) -> None:
        intervals = BattleZankouFormBuffService.infer(
            build=_build(awakening_level=1),
            actions=(),
            battle_end_us=20_000_000,
            config=_CONFIG,
        )

        self.assertEqual(2, len(intervals))
        self.assertEqual({"狩（觉醒一）", "惑（觉醒一）"}, {
            row.buff_name for row in intervals
        })
        self.assertTrue(all(
            (row.start_us, row.end_us) == (0, 20_000_000)
            for row in intervals
        ))
        self.assertEqual({0.40, 0.50}, {
            row.modifiers[0].magnitude_value for row in intervals
        })


if __name__ == "__main__":
    unittest.main()
