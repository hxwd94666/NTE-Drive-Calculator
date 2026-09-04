# 覆盖 nte-core 记录区间优先和缺证据时的 Q 动作低置信回退。
from __future__ import annotations

import unittest

from src.domain.battle_report import (
    BattleInferredAction,
    BattleObservedTimeStopInterval,
)
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
        self.assertEqual(projection.intervals, projection.q_action_intervals)
        self.assertEqual((), projection.type6_intervals)
        self.assertTrue(projection.has_unknown_types)

    def test_typed_intervals_split_q_anchors_from_type6_without_losing_clock(self) -> None:
        projection = BattleTimeStopProjectionService.resolve(
            (
                BattleObservedTimeStopInterval(1_000_000, 2_000_000, 1 << 6),
                BattleObservedTimeStopInterval(3_000_000, 4_000_000, 1 << 2),
                BattleObservedTimeStopInterval(
                    5_000_000,
                    6_000_000,
                    (1 << 4) | (1 << 6),
                ),
            ),
            (_action("q-1", "Q", 1_500_000, 3_500_000),),
        )

        self.assertEqual(
            (
                (1_000_000, 2_000_000),
                (3_000_000, 4_000_000),
                (5_000_000, 6_000_000),
            ),
            projection.intervals,
        )
        self.assertEqual(
            ((3_000_000, 4_000_000), (5_000_000, 6_000_000)),
            projection.q_action_intervals,
        )
        self.assertEqual(
            ((1_000_000, 2_000_000), (5_000_000, 6_000_000)),
            projection.type6_intervals,
        )
        self.assertEqual(
            ((3_000_000, 4_000_000),),
            projection.non_type6_intervals,
        )
        self.assertFalse(projection.has_unknown_types)

    def test_compacted_v5_unknown_stops_clock_without_becoming_q_evidence(self) -> None:
        projection = BattleTimeStopProjectionService.resolve(
            (
                BattleObservedTimeStopInterval(
                    1_000_000,
                    2_000_000,
                    None,
                    "compacted_unknown",
                ),
            ),
            (_action("q-1", "Q", 1_500_000, 1_700_000),),
        )

        self.assertEqual(((1_000_000, 2_000_000),), projection.intervals)
        self.assertEqual((), projection.q_action_intervals)
        self.assertTrue(projection.has_unknown_types)

    def test_legacy_linko_e_fallback_extends_clock_without_becoming_q_evidence(self) -> None:
        base = BattleTimeStopProjectionService.resolve(
            ((1_000_000, 2_000_000),),
            (),
        )

        projection = BattleTimeStopProjectionService.with_inferred_linko_e(
            base,
            ((2_000_000, 3_000_000),),
        )

        self.assertEqual(((1_000_000, 3_000_000),), projection.intervals)
        self.assertEqual(((1_000_000, 2_000_000),), projection.q_action_intervals)
        self.assertEqual(
            ((2_000_000, 3_000_000),),
            projection.inferred_linko_e_intervals,
        )
        self.assertEqual("低", projection.confidence)

    def test_observed_typed_intervals_preserve_missing_mask_as_unknown(self) -> None:
        intervals = BattleTimeStopProjectionService.observed_typed_intervals(
            (
                {
                    "start_unix_us": None,
                    "end_unix_us": None,
                    "pause_type_mask": 1 << 6,
                    "raw_interval": {
                        "start_offset_seconds": 1.25,
                        "end_offset_seconds": 2.75,
                        "pause_type_mask": 1 << 6,
                    },
                },
                {
                    "start_unix_us": 12_000_000,
                    "end_unix_us": 13_000_000,
                    "pause_type_mask": None,
                    "raw_interval": {},
                },
            ),
            origin_us=10_000_000,
        )

        self.assertEqual(
            (
                BattleObservedTimeStopInterval(
                    1_250_000,
                    2_750_000,
                    1 << 6,
                    "typed",
                ),
                BattleObservedTimeStopInterval(2_000_000, 3_000_000, None),
            ),
            intervals,
        )

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

    def test_counterfactual_type6_stops_clock_without_anchoring_q(self) -> None:
        evidence = {
            "contract_version": 5,
            "axis_complete": True,
            "hits": [
                {
                    "sequence_text": "1",
                    "sequence_order": 1,
                    "relative_time_us": 1_500_000,
                    "character_id": 1036,
                    "character_name": "残虹",
                    "direction": "outgoing",
                    "damage": 100,
                    "follow_up_damage": 0,
                    "ability_name": "GA_Zankou_UltraSkill",
                    "gameplay_effect_name": "GE_Player_Zankou_UltraSkill1_Damage",
                    "damage_display_name": "极轨终结伤害",
                    "damage_component": "skill",
                    "attack_type": "Q技能",
                    "damage_attribute": "incantation",
                    "follow_up_labels": [],
                    "target_id": "monster-1",
                    "target_name": "训练目标",
                }
            ],
            "time_stop_intervals": [
                {
                    "start_unix_us": None,
                    "end_unix_us": None,
                    "duration_us": 1_000_000,
                    "pause_type_mask": 1 << 6,
                    "raw_interval": {
                        "start_offset_seconds": 1.0,
                        "end_offset_seconds": 2.0,
                        "pause_type_mask": 1 << 6,
                    },
                }
            ],
        }

        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=8,
            evidence=evidence,
            build=None,
            capability_level="hit_axis",
            infer_buffs=False,
        )

        q_actions = tuple(
            action for action in analysis.inferred_actions if action.input_kind == "Q"
        )
        self.assertEqual(1_500_000, q_actions[0].start_us)
        self.assertEqual(((1_000_000, 2_000_000),), analysis.time_stop_intervals)
        self.assertEqual((), analysis.q_action_time_stop_intervals)
        self.assertEqual(((1_000_000, 2_000_000),), analysis.type6_time_stop_intervals)
        self.assertFalse(analysis.has_unknown_time_stop_types)
        self.assertAlmostEqual(100.0, analysis.effective_dps)


if __name__ == "__main__":
    unittest.main()
