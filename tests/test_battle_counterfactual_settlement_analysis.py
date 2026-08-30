# 验证最大生命结算只进入对应的正式或估计汇总。
from __future__ import annotations

import unittest

from src.services.battle_counterfactual_analysis_service import (
    BattleCounterfactualAnalysisService,
)
from tests.battle_counterfactual_fixtures import evidence_fixture as _evidence


NTE_TEST_TIER = "core"


class BattleCounterfactualSettlementAnalysisTests(unittest.TestCase):
    def test_max_hp_settlement_is_not_reported_as_hp_overlap_correction(self) -> None:
        evidence = {
            "axis_complete": True,
            "time_stop_intervals": [],
            "hits": [
                {
                    "sequence_order": 1,
                    "relative_time_us": 1_000_000,
                    "character_id": 1004,
                    "character_name": "安魂曲",
                    "direction": "outgoing",
                    "damage": 250.0,
                    "gameplay_effect_name": "GE_Player_Lacrimosa_Blood_Damage_LV6",
                    "target_id": "boss-1",
                    "target_name": "测试目标",
                    "target_hp_before": 720.0,
                    "target_hp_after": 470.0,
                    "target_max_hp": 1_000.0,
                },
                {
                    "sequence_order": 2,
                    "relative_time_us": 1_100_000,
                    "character_id": 1036,
                    "character_name": "残虹",
                    "direction": "outgoing",
                    "damage": 1.0,
                    "gameplay_effect_name": "GE_Player_Zankou_Melee1_Damage",
                    "target_id": "boss-1",
                    "target_name": "测试目标",
                    "target_hp_before": 470.0,
                    "target_hp_after": 469.0,
                    "target_max_hp": 900.0,
                },
            ],
        }
        build = {"characters": [{
            "character_id": 1004,
            "profile": {
                "awakening_selection_initialized": True,
                "selected_awaken_effect_ids": ["Effect5"],
            },
        }]}

        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=evidence,
            build=build,
            capability_level="hit_axis",
        )

        self.assertEqual(251.0, analysis.total_damage)
        self.assertEqual(47.0, analysis.max_hp_reduction_damage)
        self.assertEqual(298.0, analysis.effective_damage)
        self.assertEqual(0.0, analysis.damage_correction_total)
        self.assertEqual(0.0, analysis.damage_overlap_correction_total)

    def test_description_estimate_is_visible_but_excluded_from_effective_damage(self) -> None:
        evidence = _evidence()
        hit = dict(evidence["hits"][0])
        hit.update(
            {
                "character_id": 1004,
                "character_name": "安魂曲",
                "gameplay_effect_name": "GE_Player_Lacrimosa_Blood_Damage",
                "damage": 100,
                "follow_up_damage": 0,
                "target_max_hp": 1_000,
                "target_hp_before": 500,
            }
        )
        evidence["hits"] = [hit]
        build = {
            "characters": [
                {
                    "character_id": 1004,
                    "observed_name": "安魂曲",
                    "profile": {
                        "awakening_selection_initialized": True,
                        "selected_awaken_effect_ids": ["Effect5"],
                    },
                    "stats": [],
                }
            ]
        }

        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=evidence,
            build=build,
            capability_level="hit_axis",
        )

        self.assertEqual(100.0, analysis.total_damage)
        self.assertEqual(100.0, analysis.effective_damage)
        self.assertEqual(100.0, analysis.estimated_max_hp_reduction_damage)
        self.assertEqual(1, len(analysis.estimated_max_hp_events))
        self.assertEqual(
            100.0,
            analysis.targets[0].estimated_max_hp_reduction_damage,
        )

    def test_description_estimate_uses_half_hp_expectation_without_hp_sample(self) -> None:
        evidence = _evidence()
        hit = dict(evidence["hits"][0])
        hit.update({
            "character_id": 1004,
            "character_name": "安魂曲",
            "gameplay_effect_name": "GE_Player_Lacrimosa_Blood_Damage",
            "damage": 100,
            "follow_up_damage": 0,
        })
        hit.pop("target_hp_before", None)
        hit.pop("target_hp_after", None)
        hit.pop("target_max_hp", None)
        evidence["hits"] = [hit]
        build = {"characters": [{
            "character_id": 1004,
            "observed_name": "安魂曲",
            "profile": {
                "awakening_selection_initialized": True,
                "selected_awaken_effect_ids": ["Effect5"],
            },
            "stats": [],
        }]}

        analysis = BattleCounterfactualAnalysisService.analyze(
            battle_record_id=7,
            evidence=evidence,
            build=build,
            capability_level="hit_axis",
        )

        (event,) = analysis.estimated_max_hp_events
        self.assertEqual(100.0, event.effective_hp_loss)
        self.assertEqual(0.5, event.hp_ratio_before)
        self.assertIn("50% 生命比例期望", event.inference_basis)


if __name__ == "__main__":
    unittest.main()
