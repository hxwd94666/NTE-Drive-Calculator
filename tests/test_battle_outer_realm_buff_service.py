# 验证轨外之境赛季 Buff 只使用正式赛季配置与逐击证据重建。
from __future__ import annotations

import unittest

from src.domain.battle_report import BattleAnalysisHit
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_buff_inference_service import BattleBuffInferenceService
from src.services.battle_outer_realm_buff_service import (
    BattleOuterRealmBuffComponent,
    BattleOuterRealmBuffConfig,
    BattleOuterRealmBuffService,
)


def _hit(
    sequence: int,
    time_us: int,
    *,
    effect: str,
    target_id: str = "target-a",
    attribute: str = "incantation",
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=f"{sequence}:primary",
        sequence=sequence,
        relative_time_us=time_us,
        character_id=1036,
        character_name="残虹",
        skill_name="测试技能",
        damage_name=effect,
        damage_component="reaction" if effect.startswith("Buff_Reaction") else "skill",
        attack_type="reaction" if effect.startswith("Buff_Reaction") else "normal",
        damage_attribute=attribute,
        target_id=target_id,
        target_name="训练目标",
        damage=1000.0,
        direction="outgoing",
        is_follow_up=False,
        classification="reaction" if effect.startswith("Buff_Reaction") else "direct",
        gameplay_effect_id=effect,
    )


def _p8() -> BattleOuterRealmBuffConfig:
    return BattleOuterRealmBuffConfig(
        level_config_id="Abyss_8",
        season_name="烛天环线",
        buff_id="abyss_8_buff_01",
        buff_name="炽火灼痕",
        description="测试",
        gameplay_effect_path="/Game/Test/Buff_Abyss_Phase_008",
        components=(
            BattleOuterRealmBuffComponent(
                component_ordinal=0,
                trigger_kind="corruption_damage_stack",
                property_id="CritDamageBase",
                value=0.06,
                duration_seconds=6.0,
                trigger_cooldown_seconds=1.0,
                stack_limit_count=8,
            ),
            BattleOuterRealmBuffComponent(
                component_ordinal=1,
                trigger_kind="while_target_toppled",
                property_id="DamageUpGeneralBase",
                value=0.25,
            ),
        ),
        topple_limit=50.0,
        topple_recovery_speed=5.0,
    )


class BattleOuterRealmBuffServiceTests(unittest.TestCase):
    def test_corruption_stacks_after_hit_with_one_second_cap_and_refresh(self):
        hits = (
            _hit(1, 1_000_000, effect="Buff_Reaction_5_new_1036"),
            _hit(2, 1_700_000, effect="Buff_Reaction_5_new_1036"),
            _hit(3, 2_100_000, effect="Buff_Reaction_5_new_1036"),
        )
        intervals = BattleOuterRealmBuffService.infer(
            _p8(),
            hits=hits,
            battle_end_us=10_000_000,
        )

        self.assertEqual(
            1,
            BattleBuffInferenceService.active_for_hit(intervals, hits[2])[0].stacks,
        )
        after_third = _hit(4, 2_100_001, effect="GE_Player_Test_Damage")
        active = BattleBuffInferenceService.active_for_hit(intervals, after_third)
        self.assertEqual(2, active[0].stacks)
        self.assertEqual(0.12, active[0].modifiers[0].magnitude_value * active[0].stacks)
        self.assertEqual(8_100_000, active[0].end_us)

    def test_topple_damage_starts_target_specific_limit_over_recovery_window(self):
        topple = _hit(1, 2_000_000, effect="Buff_Tenacity_damage")
        same_target = _hit(2, 8_000_000, effect="GE_Player_Test_Damage")
        other_target = _hit(
            3,
            8_000_000,
            effect="GE_Player_Test_Damage",
            target_id="target-b",
        )
        intervals = BattleOuterRealmBuffService.infer(
            _p8(),
            hits=(topple, same_target, other_target),
            battle_end_us=20_000_000,
        )
        topple_interval = next(
            row for row in intervals if row.trigger_event_type == "TARGET_TOPPLED"
        )

        self.assertEqual(2_000_001, topple_interval.start_us)
        self.assertEqual(12_000_000, topple_interval.end_us)
        same_projection = BattleBuffAttributeProjectionService.project_hit(
            same_target,
            intervals,
        )
        other_projection = BattleBuffAttributeProjectionService.project_hit(
            other_target,
            intervals,
        )
        self.assertEqual(0.25, same_projection.modifiers[0].additive_value)
        self.assertEqual((), other_projection.modifiers)

    def test_p9_whole_battle_modifiers_follow_damage_attribute(self):
        config = BattleOuterRealmBuffConfig(
            level_config_id="Abyss_9",
            season_name="幽语环线",
            buff_id="abyss_9_buff_01",
            buff_name="飘摇残响",
            description="测试",
            gameplay_effect_path="/Game/Test/Buff_Abyss_Phase_009",
            components=(
                BattleOuterRealmBuffComponent(0, "whole_battle", "DamageUpNatureBase", 0.45),
                BattleOuterRealmBuffComponent(1, "whole_battle", "DamageUpIncantationBase", 0.45),
            ),
        )
        hits = (
            _hit(1, 1_000_000, effect="GE_Test", attribute="nature"),
            _hit(2, 2_000_000, effect="GE_Test", attribute="incantation"),
        )
        intervals = BattleOuterRealmBuffService.infer(
            config,
            hits=hits,
            battle_end_us=3_000_000,
        )

        for hit, property_id in zip(
            hits,
            ("DamageUpNatureBase", "DamageUpIncantationBase"),
            strict=True,
        ):
            projection = BattleBuffAttributeProjectionService.project_hit(hit, intervals)
            self.assertEqual(property_id, projection.modifiers[0].property_id)
            self.assertEqual(0.45, projection.modifiers[0].additive_value)


if __name__ == "__main__":
    unittest.main()
