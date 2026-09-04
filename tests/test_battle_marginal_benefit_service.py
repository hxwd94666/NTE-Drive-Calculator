# 验证任意品质空幕识别与金色满级主属性候选构造。
from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from src.domain.battle_counterfactual_quantification import (
    BattleDamageQuantification,
)
from src.domain.battle_marginal_benefit import BattleMarginalDelta
from src.domain.battle_report import BattleAnalysisSnapshot
from src.services.battle_marginal_benefit_service import (
    BattleMarginalBenefitService,
)
from src.services.battle_marginal_candidate_service import (
    BattleMarginalCandidateService,
)


CHARACTER_ID = 1003


def _snapshot() -> BattleAnalysisSnapshot:
    return BattleAnalysisSnapshot(
        battle_record_id=7,
        capability_level="hit_axis",
        axis_complete=True,
        formula_model_version="fixture",
        name_mapping_version="fixture",
        action_inference_version="fixture",
        timeline_projection_version="fixture",
        battle_start_us=0,
        battle_end_us=1_000_000,
        timeline_end_us=1_000_000,
        range_start_us=0,
        range_end_us=1_000_000,
        duration_seconds=1.0,
        total_damage=100.0,
        total_dps=100.0,
        timeline_hits=(),
        inferred_actions=(),
        inferred_inputs=(),
        timeline_damage_groups=(),
        hits=(),
        roles=(),
        skills=(),
        targets=(),
        baselines=(),
        effective_damage=100.0,
        effective_dps=100.0,
    )


def _delta() -> BattleMarginalDelta:
    return BattleMarginalDelta(
        team_status="complete",
        role_status="complete",
        baseline_team_damage=100.0,
        projected_team_damage=110.0,
        team_gain_damage=10.0,
        team_gain_percent=10.0,
        baseline_role_damage=100.0,
        projected_role_damage=110.0,
        role_gain_damage=10.0,
        role_gain_percent=10.0,
        team_coverage_percent=100.0,
        role_coverage_percent=100.0,
    )


def _complete_comparison(
    baseline_team: float,
    projected_team: float,
    baseline_role: float,
    projected_role: float,
) -> SimpleNamespace:
    team_quantification = BattleDamageQuantification(
        status="complete",
        basis_damage=baseline_team,
        fully_quantified_damage=baseline_team,
        partially_quantified_damage=0.0,
        unavailable_damage=0.0,
        proven_unchanged_damage=0.0,
        quantified_increment=projected_team - baseline_team,
    )
    role_quantification = BattleDamageQuantification(
        status="complete",
        basis_damage=baseline_role,
        fully_quantified_damage=baseline_role,
        partially_quantified_damage=0.0,
        unavailable_damage=0.0,
        proven_unchanged_damage=0.0,
        quantified_increment=projected_role - baseline_role,
    )
    return SimpleNamespace(
        baseline_damage=baseline_team,
        known_projection_damage=projected_team,
        quantification=team_quantification,
        roles=(SimpleNamespace(
            character_id=CHARACTER_ID,
            baseline_damage=baseline_role,
            known_projection_damage=projected_role,
            quantification=role_quantification,
        ),),
    )


class BattleMarginalBenefitServiceTests(unittest.TestCase):
    def test_fork_ab_c_uses_shared_materialized_endpoints(self) -> None:
        no_fork = replace(
            _snapshot(),
            effective_damage=100.0,
            roles=(SimpleNamespace(character_id=CHARACTER_ID, damage=40.0),),
        )
        stats_only = replace(
            _snapshot(),
            effective_damage=130.0,
            roles=(SimpleNamespace(character_id=CHARACTER_ID, damage=55.0),),
        )
        current = replace(
            _snapshot(),
            effective_damage=150.0,
            roles=(SimpleNamespace(character_id=CHARACTER_ID, damage=70.0),),
            baselines=(SimpleNamespace(
                character_id=CHARACTER_ID,
                stats=(SimpleNamespace(property_id="AtkUp", value=1.0),),
            ),),
        )
        candidate = BattleMarginalCandidateService.freeze(
            7,
            [{"character_id": CHARACTER_ID, "fork_id": "fork_test"}],
            equipment_editable=True,
        )
        comparisons = (
            _complete_comparison(100.0, 160.0, 40.0, 75.0),
            _complete_comparison(100.0, 135.0, 40.0, 58.0),
            _complete_comparison(130.0, 155.0, 55.0, 72.0),
        )

        with (
            patch.object(
                BattleMarginalBenefitService,
                "_materialize_variant",
                side_effect=(no_fork, stats_only),
            ),
            patch(
                "src.services.battle_marginal_benefit_service."
                "BattleBuildCounterfactualService.compare",
                side_effect=comparisons,
            ),
        ):
            result = BattleMarginalBenefitService._fork_benefit(
                current=current,
                candidate=candidate,
                profile=candidate.profiles[0],
                character_id=CHARACTER_ID,
                fork_names={"fork_test": "测试弧盘"},
                load_variant=lambda _candidate: _snapshot(),
                progress_callback=None,
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(30.0, result.permanent.team_gain_damage)
        self.assertEqual(20.0, result.skill.team_gain_damage)
        self.assertEqual(50.0, result.comprehensive.team_gain_damage)
        self.assertEqual(15.0, result.permanent.role_gain_damage)
        self.assertEqual(15.0, result.skill.role_gain_damage)
        self.assertEqual(30.0, result.comprehensive.role_gain_damage)
        self.assertAlmostEqual(0.0, result.closure_team_damage)
        self.assertAlmostEqual(0.0, result.closure_role_damage)

    def test_gold_main_value_accepts_capture_float_noise(self) -> None:
        self.assertTrue(BattleMarginalBenefitService._same_stat_value(
            0.30,
            0.300000011920929,
        ))
        self.assertFalse(BattleMarginalBenefitService._same_stat_value(
            0.375,
            0.300000011920929,
        ))

    def test_purple_current_core_builds_gold_max_same_property_candidate(
        self,
    ) -> None:
        profile = {
            "character_id": CHARACTER_ID,
            "equipment_override": [{
                "kind": "core",
                "quality": "purple",
                "level": 10,
                "suit_id": "Suit_Test",
                "stats": [
                    {
                        "stat_group": "main",
                        "property_id": "AtkUp",
                        "value": 0.20,
                        "is_percent": True,
                    },
                    {
                        "stat_group": "sub",
                        "property_id": "CritBase",
                        "value": 0.04,
                        "is_percent": True,
                    },
                ],
            }],
        }
        candidate = BattleMarginalCandidateService.freeze(
            7,
            [profile],
            equipment_editable=True,
        )
        loaded_candidates = []

        def load_variant(variant):
            loaded_candidates.append(variant)
            return _snapshot()

        with (
            patch.object(
                BattleMarginalBenefitService,
                "_materialize_variant",
                return_value=_snapshot(),
            ),
            patch(
                "src.services.battle_marginal_benefit_service."
                "BattleBuildCounterfactualService.compare",
                return_value=object(),
            ),
            patch(
                "src.services.battle_marginal_benefit_service."
                "BattleBuildTimelineProjectionService.project",
                return_value=_snapshot(),
            ),
            patch.object(
                BattleMarginalBenefitService,
                "_delta",
                return_value=_delta(),
            ),
        ):
            rows, notice = BattleMarginalBenefitService._core_main_stats(
                current=_snapshot(),
                candidate=candidate,
                profile=profile,
                character_id=CHARACTER_ID,
                core_catalog={"AtkUp": ("攻击力提升", True, 0.30)},
                load_variant=load_variant,
                progress_callback=None,
            )

        self.assertEqual("", notice)
        self.assertEqual(1, len(rows))
        self.assertTrue(rows[0].is_current)
        self.assertEqual(0.30, rows[0].value)
        self.assertEqual(2, len(loaded_candidates))
        no_main_core = loaded_candidates[0].profiles[0]["equipment_override"][0]
        gold_main_core = loaded_candidates[1].profiles[0]["equipment_override"][0]
        self.assertEqual("purple", gold_main_core["quality"])
        self.assertEqual(10, gold_main_core["level"])
        self.assertEqual("Suit_Test", gold_main_core["suit_id"])
        self.assertFalse(any(
            row["stat_group"] == "main" for row in no_main_core["stats"]
        ))
        gold_main = next(
            row for row in gold_main_core["stats"]
            if row["stat_group"] == "main"
        )
        self.assertEqual("AtkUp", gold_main["property_id"])
        self.assertEqual(0.30, gold_main["value"])
        self.assertTrue(any(
            row["stat_group"] == "sub" for row in gold_main_core["stats"]
        ))


if __name__ == "__main__":
    unittest.main()
