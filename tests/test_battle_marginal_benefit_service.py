# 验证任意品质空幕识别与金色满级主属性候选构造。
from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from src.domain.battle_counterfactual import BattleBuildHitCounterfactual
from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleDamageQuantification,
    BattleQuantificationGap,
)
from src.domain.battle_marginal_benefit import BattleMarginalDelta
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleHitReplayFactor,
    BattleHitReplayResult,
)
from src.services.battle_marginal_benefit_service import (
    BattleMarginalBenefitService,
)
from src.services.battle_marginal_candidate_service import (
    BattleMarginalCandidateService,
)
from src.services.battle_marginal_benefit_scope import (
    prepare_marginal_benefit_role_scope,
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
    *,
    role_character_id: int = CHARACTER_ID,
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
    hit_quantification = BattleCounterfactualRatio.complete(
        projected_role / baseline_role,
        method="fixture",
        confidence="高",
        dependency_scope="character_only",
        included_dimension_ids=("fixture",),
        explanation="fixture",
    )
    return SimpleNamespace(
        baseline_damage=baseline_team,
        known_projection_damage=projected_team,
        quantification=team_quantification,
        roles=(SimpleNamespace(
            character_id=role_character_id,
            baseline_damage=baseline_role,
            known_projection_damage=projected_role,
            quantification=role_quantification,
        ),),
        hits=(BattleBuildHitCounterfactual(
            event_id="panel-hit",
            character_id=role_character_id,
            character_name="测试角色",
            skill_name="测试技能",
            damage_name="测试伤害",
            baseline_damage=baseline_role,
            known_projection_damage=projected_role,
            candidate_damage=projected_role,
            heuristic_projection_damage=None,
            quantification=hit_quantification,
        ),),
        vital_events=(),
    )


def _panel_hit(
    damage: float,
    *,
    character_id: int = CHARACTER_ID,
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id="panel-hit",
        sequence=1,
        relative_time_us=100_000,
        character_id=character_id,
        character_name="残虹" if character_id == 1036 else "测试角色",
        skill_name="同频·Effect6",
        damage_name="同频伤害",
        damage_component="direct",
        attack_type="QTE",
        damage_attribute="incantation",
        target_id="target:1",
        target_name="目标",
        damage=damage,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
    )


def _panel_replay(hit: BattleAnalysisHit) -> BattleHitReplayResult:
    return BattleHitReplayResult(
        event_id=hit.event_id,
        observed_damage=hit.damage,
        non_critical_damage=hit.damage,
        critical_damage=None,
        selected_damage=hit.damage,
        selected_error_percent=0.0,
        critical_state="non_critical",
        confidence="高",
        factors=(),
        expected_damage=hit.damage,
        critical_policy="character",
        formula_panel_character_id=1072,
        formula_context_kind="linko_coattack:skill",
    )


class BattleMarginalBenefitServiceTests(unittest.TestCase):
    def test_fork_ab_c_uses_shared_materialized_endpoints(self) -> None:
        no_fork = replace(
            _snapshot(),
            effective_damage=100.0,
            hits=(_panel_hit(40.0),),
            roles=(SimpleNamespace(character_id=CHARACTER_ID, damage=40.0),),
        )
        stats_only = replace(
            _snapshot(),
            effective_damage=130.0,
            hits=(_panel_hit(55.0),),
            roles=(SimpleNamespace(character_id=CHARACTER_ID, damage=55.0),),
        )
        current = replace(
            _snapshot(),
            effective_damage=150.0,
            hits=(_panel_hit(70.0),),
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
                role_scope=prepare_marginal_benefit_role_scope(
                    current,
                    CHARACTER_ID,
                ),
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

    def test_linko_panel_delta_uses_formula_owner_without_rewriting_raw_roles(
        self,
    ) -> None:
        hit = _panel_hit(100.0, character_id=1036)
        current = replace(
            _snapshot(),
            hits=(hit,),
            timeline_hits=(hit,),
            hit_replays=(_panel_replay(hit),),
            effective_damage=100.0,
            roles=(SimpleNamespace(character_id=1036, damage=100.0),),
        )
        role_scope = prepare_marginal_benefit_role_scope(
            current,
            1072,
        )
        comparison = _complete_comparison(
            100.0,
            120.0,
            100.0,
            120.0,
            role_character_id=1036,
        )
        awakening_gap = BattleQuantificationGap(
            code="linko_effect6_resource_restore_unquantified",
            dimension_id="linko_awaken_effect6_resource_restore",
            dependency_scope="mechanic_specific",
            property_ids=(),
            explanation="灵可六觉资源回复未生成额外事件。",
        )
        comparison.quantification = replace(
            comparison.quantification,
            status="partial",
            gaps=(awakening_gap,),
        )

        delta = BattleMarginalBenefitService._delta(
            comparison,
            role_scope,
        )
        unchanged = BattleMarginalBenefitService._unchanged_delta(
            current,
            role_scope,
        )

        self.assertEqual(
            ("panel-hit",),
            tuple(row.event_id for row in role_scope.hit_shares),
        )
        self.assertEqual(20.0, delta.team_gain_damage)
        self.assertEqual(20.0, delta.role_gain_damage)
        self.assertEqual(20.0, delta.team_gain_percent)
        self.assertEqual(20.0, delta.role_gain_percent)
        self.assertEqual(100.0, unchanged.baseline_role_damage)
        self.assertEqual("partial", delta.role_status)
        self.assertIn(awakening_gap.explanation, delta.gap_explanations)
        self.assertEqual((1036,), tuple(
            row.character_id for row in comparison.roles
        ))

    def test_linko_topple_uses_own_share_but_keeps_full_packet_increment(
        self,
    ) -> None:
        hit = replace(
            _panel_hit(220.0, character_id=1036),
            classification="topple",
            skill_name="倾陷伤害",
            damage_name="倾陷伤害",
            attack_type="倾陷伤害",
        )
        replay = replace(
            _panel_replay(hit),
            formula_panel_character_id=None,
            factors=(
                BattleHitReplayFactor(
                    "topple_character:1072",
                    "灵可倾陷贡献",
                    104.0,
                    "fixture",
                ),
                BattleHitReplayFactor(
                    "topple_character:1036",
                    "残虹倾陷贡献",
                    116.0,
                    "fixture",
                ),
            ),
        )
        current = replace(
            _snapshot(),
            hits=(hit,),
            timeline_hits=(hit,),
            hit_replays=(replay,),
            effective_damage=220.0,
            roles=(SimpleNamespace(character_id=1036, damage=220.0),),
        )
        role_scope = prepare_marginal_benefit_role_scope(current, 1072)
        comparison = _complete_comparison(
            220.0,
            226.0,
            220.0,
            226.0,
            role_character_id=1036,
        )

        delta = BattleMarginalBenefitService._delta(comparison, role_scope)
        unchanged = BattleMarginalBenefitService._unchanged_delta(
            current,
            role_scope,
        )

        self.assertAlmostEqual(104.0, delta.baseline_role_damage)
        self.assertAlmostEqual(110.0, delta.projected_role_damage)
        self.assertAlmostEqual(6.0, delta.role_gain_damage)
        self.assertAlmostEqual(delta.team_gain_damage, delta.role_gain_damage)
        self.assertAlmostEqual(104.0, unchanged.baseline_role_damage)

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
        self._assert_gold_main_counterfactual(equipment_editable=True)

    def test_imported_core_main_variants_reach_build_projection(self) -> None:
        self._assert_gold_main_counterfactual(equipment_editable=False)

    def _assert_gold_main_counterfactual(self, *, equipment_editable: bool) -> None:
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
            equipment_editable=equipment_editable,
        )
        loaded_candidates = []

        def load_variant(variant):
            self.assertEqual(equipment_editable, variant.equipment_editable)
            loaded_candidates.append(
                BattleMarginalCandidateService.as_build_edit(
                    variant,
                    frozen_build={"characters": [{
                        "character_id": CHARACTER_ID,
                        "equipment": profile["equipment_override"],
                    }]},
                ),
            )
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
                role_scope=prepare_marginal_benefit_role_scope(
                    _snapshot(),
                    CHARACTER_ID,
                ),
                core_catalog={"AtkUp": ("攻击力提升", True, 0.30)},
                load_variant=load_variant,
                progress_callback=None,
            )

        self.assertEqual("", notice)
        self.assertEqual(1, len(rows))
        self.assertTrue(rows[0].is_current)
        self.assertEqual(0.30, rows[0].value)
        self.assertEqual(2, len(loaded_candidates))
        no_main_core = loaded_candidates[0]["characters"][0]["profile"][
            "equipment_override"
        ][0]
        gold_main_core = loaded_candidates[1]["characters"][0]["profile"][
            "equipment_override"
        ][0]
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
        self.assertIsNone(candidate.core_main_stat_counterfactual)
        self.assertEqual(
            0.20, profile["equipment_override"][0]["stats"][0]["value"],
        )


if __name__ == "__main__":
    unittest.main()
