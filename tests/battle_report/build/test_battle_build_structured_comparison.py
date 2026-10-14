# 验证已重放公式直接比较与旧组件路径一致，未知公式仍保留证据缺口。
from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from src.domain.battle_report.timeline.events import BattleAnalysisHit
from src.domain.battle_report.analysis.snapshot import BattleAnalysisSnapshot
from src.domain.battle_report.replay.models import BattleHitReplayResult
from src.domain.battle_report.analysis.composition import BattleRangeRoleSummary
from src.services.battle_report.build.counterfactual import BattleBuildCounterfactualService
from src.services.battle_report.replay.buff_cache import BattleHitBuffProjectionCache


_SERVICE = "src.services.battle_report.build.counterfactual"


def _snapshot() -> BattleAnalysisSnapshot:
    hit = BattleAnalysisHit(
        event_id="hit-1", sequence=1, relative_time_us=100_000,
        character_id=1, character_name="角色", skill_name="技能", damage_name="技能",
        damage_component="direct", attack_type="skill", damage_attribute="chaos",
        target_id="target", target_name="目标", damage=200.0,
        direction="outgoing", is_follow_up=False, classification="direct",
    )
    replay = BattleHitReplayResult(
        event_id=hit.event_id, observed_damage=200.0, non_critical_damage=100.0,
        critical_damage=200.0, selected_damage=200.0, selected_error_percent=0.0,
        critical_state="critical", confidence="高", factors=(),
        expected_damage=150.0, critical_policy="character",
    )
    return BattleAnalysisSnapshot(
        battle_record_id=1, capability_level="formal_hit", axis_complete=True,
        formula_model_version="fixture", name_mapping_version="fixture",
        action_inference_version="fixture", timeline_projection_version="fixture",
        battle_start_us=0, battle_end_us=1_000_000, timeline_end_us=1_000_000,
        range_start_us=0, range_end_us=1_000_000, duration_seconds=1.0,
        total_damage=200.0, total_dps=200.0, timeline_hits=(hit,), hits=(hit,),
        inferred_actions=(), inferred_inputs=(), timeline_damage_groups=(),
        roles=(BattleRangeRoleSummary(
            character_id=1, character_name="角色", hits=1, damage=200.0,
            dps=200.0, share_percent=100.0,
        ),),
        skills=(), targets=(), baselines=(), effective_damage=200.0,
        effective_dps=200.0, hit_replays=(replay,),
    )


class BattleBuildStructuredComparisonTests(unittest.TestCase):
    def test_replayed_expectation_skips_projection_and_matches_component_path(self) -> None:
        original = _snapshot()
        candidate = replace(original, hit_replays=(replace(
            original.hit_replays[0], expected_damage=260.0,
            selected_damage=120.0, critical_state="non_critical",
        ),))
        # The reference enters the original comparison path, which resolves
        # projections before selecting the same formal replay expectation.
        with patch(f"{_SERVICE}.structured_formula_ratio", return_value=None):
            reference = BattleBuildCounterfactualService.compare(original=original, candidate=candidate)
        with patch.object(BattleHitBuffProjectionCache, "project", side_effect=AssertionError(
            "A resolved formula pair must not request another projection",
        )), patch("src.services.battle_report.build.comparison_batch.BattleTargetInstanceMappingService.analysis_for_hit", side_effect=AssertionError(
            "Replay already froze this target condition",
        )):
            result = BattleBuildCounterfactualService.compare(original=original, candidate=candidate)
        self.assertEqual(reference, result)
        self.assertEqual("structured_expected", result.hits[0].quantification.method)
        self.assertAlmostEqual(200.0 * (260.0 / 150.0), result.hits[0].candidate_damage)

    def test_disabled_critical_policy_preserves_noncritical_ratio(self) -> None:
        original = _snapshot()
        original = replace(original, hit_replays=(replace(
            original.hit_replays[0], critical_policy="disabled",
        ),))
        candidate = replace(original, hit_replays=(replace(
            original.hit_replays[0], non_critical_damage=125.0, expected_damage=999.0,
        ),))
        with patch(f"{_SERVICE}.structured_formula_ratio", return_value=None):
            reference = BattleBuildCounterfactualService.compare(original=original, candidate=candidate)
        result = BattleBuildCounterfactualService.compare(original=original, candidate=candidate)
        self.assertEqual(reference, result)
        self.assertEqual("structured_selected", result.hits[0].quantification.method)
        self.assertEqual(250.0, result.hits[0].candidate_damage)

    def test_unknown_zero_and_nonfinite_formulas_keep_component_evidence_boundary(self) -> None:
        original = _snapshot()
        replay = original.hit_replays[0]
        candidates = (
            replace(replay, critical_policy="unknown"),
            replace(replay, critical_state="unreplayable"),
            replace(replay, expected_damage=0.0),
            replace(replay, expected_damage=float("inf")),
        )
        for candidate_replay in candidates:
            with self.subTest(replay=candidate_replay):
                candidate = replace(original, hit_replays=(candidate_replay,))
                with patch(f"{_SERVICE}.structured_formula_ratio", return_value=None):
                    reference = BattleBuildCounterfactualService.compare(original=original, candidate=candidate)
                result = BattleBuildCounterfactualService.compare(original=original, candidate=candidate)
                self.assertEqual(reference, result)
                self.assertEqual("unavailable", result.hits[0].quantification.status)
                self.assertIsNone(result.hits[0].candidate_damage)

    def test_finite_pair_with_overflowing_ratio_does_not_bypass_fallback(self) -> None:
        original = _snapshot()
        original = replace(original, hit_replays=(replace(
            original.hit_replays[0], expected_damage=1e-308,
        ),))
        candidate = replace(original, hit_replays=(replace(
            original.hit_replays[0], expected_damage=1e308,
        ),))
        with patch(f"{_SERVICE}.structured_formula_ratio", return_value=None):
            reference = BattleBuildCounterfactualService.compare(original=original, candidate=candidate)
        result = BattleBuildCounterfactualService.compare(original=original, candidate=candidate)
        self.assertEqual(reference, result)
        self.assertEqual("unavailable", result.hits[0].quantification.status)
        self.assertIsNone(result.hits[0].candidate_damage)

    def test_mixed_axis_projects_only_unresolved_hits_and_keeps_order(self) -> None:
        original = _snapshot()
        unresolved = replace(original.hits[0], event_id="hit-2", sequence=2, damage=100.0)
        hits = (*original.hits, unresolved)
        original = replace(
            original, hits=hits, timeline_hits=hits, total_damage=300.0,
            effective_damage=300.0, total_dps=300.0, effective_dps=300.0,
            roles=(replace(original.roles[0], hits=2, damage=300.0, dps=300.0),),
        )
        candidate = replace(original, hit_replays=(replace(
            original.hit_replays[0], expected_damage=300.0,
        ),))
        with patch(f"{_SERVICE}.structured_formula_ratio", return_value=None):
            reference = BattleBuildCounterfactualService.compare(original=original, candidate=candidate)
        projected_events: list[str] = []
        project = BattleHitBuffProjectionCache.project

        def record_projection(cache, hit):
            projected_events.append(hit.event_id)
            return project(cache, hit)

        with patch.object(BattleHitBuffProjectionCache, "project", autospec=True, side_effect=record_projection):
            result = BattleBuildCounterfactualService.compare(original=original, candidate=candidate)
        self.assertEqual(reference, result)
        self.assertEqual(["hit-2", "hit-2"], projected_events)
        self.assertEqual(["hit-1", "hit-2"], [row.event_id for row in result.hits])
        self.assertEqual("complete", result.hits[0].quantification.status)
        self.assertEqual("unavailable", result.hits[1].quantification.status)
