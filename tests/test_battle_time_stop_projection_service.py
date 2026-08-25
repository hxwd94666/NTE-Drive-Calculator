# 覆盖正式时停优先和缺证据时的 Q 动作低置信回退。
from __future__ import annotations

import unittest

from src.domain.battle_report import BattleInferredAction
from src.services.battle_action_inference_service import (
    BattleActionAnimationCandidate,
)
from src.services.battle_counterfactual_analysis_service import (
    BattleCounterfactualAnalysisService,
)
from src.services.battle_time_stop_projection_service import (
    BattleTimeStopProjectionService,
)


def _action(
    action_id: str,
    input_kind: str,
    start_us: int,
    end_us: int,
) -> BattleInferredAction:
    return BattleInferredAction(
        action_id=action_id,
        character_id=1072,
        character_name="灵可",
        action_name="极轨终结" if input_kind == "Q" else "普通攻击",
        input_kind=input_kind,
        input_sequence=input_kind,
        start_us=start_us,
        end_us=end_us,
        hits=1,
        damage=100.0,
        identity_confidence="中",
        timing_confidence="中",
        inference_basis="测试动作边界",
        evidence_event_ids=(f"{action_id}:direct",),
        gameplay_effect_ids=(),
    )


class BattleTimeStopProjectionServiceTests(unittest.TestCase):
    def test_observed_interval_takes_precedence_over_q_actions(self) -> None:
        projection = BattleTimeStopProjectionService.resolve(
            ((1_000_000, 2_000_000),),
            (_action("q-1", "Q", 3_000_000, 5_000_000),),
        )

        self.assertEqual(((1_000_000, 2_000_000),), projection.intervals)
        self.assertEqual("nte_core", projection.source_kind)
        self.assertEqual("高", projection.confidence)

    def test_no_usable_observed_interval_falls_back_to_merged_q_windows(self) -> None:
        projection = BattleTimeStopProjectionService.resolve(
            ((1_000_000, None),),
            (
                _action("q-1", "Q", 1_000_000, 3_000_000),
                _action("a-1", "A", 2_000_000, 8_000_000),
                _action("q-2", "Q", 3_000_000, 4_500_000),
                _action("q-3", "Q", 7_000_000, 9_000_000),
            ),
        )

        self.assertEqual(
            ((1_000_000, 4_500_000), (7_000_000, 9_000_000)),
            projection.intervals,
        )
        self.assertEqual("inferred_q_action", projection.source_kind)
        self.assertEqual("低", projection.confidence)
        self.assertIn("Q 动作", projection.inference_basis)

    def test_missing_observed_intervals_and_q_actions_stays_empty(self) -> None:
        projection = BattleTimeStopProjectionService.resolve(
            (),
            (_action("a-1", "A", 1_000_000, 2_000_000),),
        )

        self.assertEqual((), projection.intervals)
        self.assertEqual("none", projection.source_kind)
        self.assertEqual("", projection.confidence)

    def test_counterfactual_analysis_uses_q_fallback_for_active_clock(self) -> None:
        evidence = {
            "axis_complete": True,
            "hits": [
                {
                    "sequence_text": "1",
                    "sequence_order": 1,
                    "relative_time_us": 3_000_000,
                    "character_id": 1072,
                    "character_name": "灵可",
                    "direction": "outgoing",
                    "damage": 100,
                    "follow_up_damage": 0,
                    "ability_name": "GA_Lingke_UltraSkill",
                    "gameplay_effect_name": "GE_Player_Lingke_UltraSkill1_Damage",
                    "damage_display_name": "极轨终结伤害",
                    "damage_component": "skill",
                    "attack_type": "Q技能",
                    "damage_attribute": "nature",
                    "follow_up_labels": [],
                    "target_id": "monster-1",
                    "target_name": "训练目标",
                },
                {
                    "sequence_text": "2",
                    "sequence_order": 2,
                    "relative_time_us": 8_000_000,
                    "character_id": 1072,
                    "character_name": "灵可",
                    "direction": "outgoing",
                    "damage": 100,
                    "follow_up_damage": 0,
                    "ability_name": "GA_Lingke_Melee",
                    "gameplay_effect_name": "GE_Player_Lingke_Melee1_Damage",
                    "damage_display_name": "普通攻击伤害",
                    "damage_component": "skill",
                    "attack_type": "普攻",
                    "damage_attribute": "nature",
                    "follow_up_labels": [],
                    "target_id": "monster-1",
                    "target_name": "训练目标",
                },
            ],
            "time_stop_intervals": [],
        }
        candidate = BattleActionAnimationCandidate(
            ability_id="GA_Lingke_UltraSkill",
            selector_key="",
            montage_asset_path="/Game/Test/Q",
            effect_hit_offsets_us=(
                ("GE_Player_Lingke_UltraSkill1_Damage", (1_000_000,)),
            ),
            trigger_end_offsets_us=(4_000_000,),
            end_event_offsets_us=(),
            section_end_offsets_us=(),
            duration_us=4_000_000,
        )

        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=evidence,
            build=None,
            capability_level="hit_axis",
            animation_candidates=(candidate,),
            infer_buffs=False,
        )

        self.assertEqual((), analysis.observed_time_stop_intervals)
        self.assertEqual(((2_000_000, 6_000_000),), analysis.time_stop_intervals)
        self.assertEqual("inferred_q_action", analysis.time_stop_source_kind)
        self.assertEqual("低", analysis.time_stop_confidence)


if __name__ == "__main__":
    unittest.main()
