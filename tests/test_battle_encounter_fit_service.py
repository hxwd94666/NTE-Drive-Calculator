# 验证同血量遭遇候选只用未校正逐击公式进行稳定残差选择。
"""Public behavior for deterministic robust encounter fitting."""

from __future__ import annotations

import unittest

from src.services.battle_encounter_fit_service import (
    BattleEncounterFitCandidate,
    BattleEncounterFitPrediction,
    BattleEncounterFitService,
)


def _hit(
    event_id: str,
    observed: float,
    predicted: float,
    *,
    group_id: str = "",
) -> BattleEncounterFitPrediction:
    return BattleEncounterFitPrediction(
        event_id=event_id,
        group_id=group_id or event_id,
        observed_damage=observed,
        non_critical_damage=predicted,
    )


class BattleEncounterFitServiceTests(unittest.TestCase):
    def test_lower_raw_prediction_residual_wins_even_with_low_confidence(self) -> None:
        close = BattleEncounterFitCandidate(
            candidate_ref="DiyBossStage3",
            predictions=(_hit("hit-1", 100.0, 98.0, group_id="skill-a"),),
        )
        far = BattleEncounterFitCandidate(
            candidate_ref="DiyBossStage4",
            predictions=(_hit("hit-1", 100.0, 60.0, group_id="skill-a"),),
        )

        result = BattleEncounterFitService.select((far, close))

        self.assertEqual("DiyBossStage3", result.winner_ref)
        self.assertEqual("robust_fit", result.selection_mode)
        self.assertEqual("低", result.confidence)
        self.assertTrue(result.ambiguous)
        self.assertGreater(result.score_gap, 0.0)
        self.assertIn("corrected_expected_damage 未进入评分", result.audit_summary)

    def test_critical_mixture_uses_raw_branches_and_inferred_probability(self) -> None:
        matching = BattleEncounterFitCandidate(
            candidate_ref="matching",
            predictions=(BattleEncounterFitPrediction(
                event_id="critical-hit",
                group_id="skill-a",
                observed_damage=200.0,
                non_critical_damage=100.0,
                critical_damage=200.0,
                expected_damage=125.0,
            ),),
        )
        shifted = BattleEncounterFitCandidate(
            candidate_ref="shifted",
            predictions=(BattleEncounterFitPrediction(
                event_id="critical-hit",
                group_id="skill-a",
                observed_damage=200.0,
                non_critical_damage=60.0,
                critical_damage=120.0,
                expected_damage=75.0,
            ),),
        )

        result = BattleEncounterFitService.select((shifted, matching))
        winner = next(row for row in result.scores if row.candidate_ref == "matching")
        audit = next(row for row in winner.hit_audits if row.eligible)

        self.assertEqual("matching", result.winner_ref)
        self.assertEqual("critical_mixture", audit.prediction_mode)
        self.assertAlmostEqual(0.25, audit.critical_probability)
        self.assertIsNotNone(audit.non_critical_log_residual)
        self.assertIsNotNone(audit.critical_log_residual)

    def test_missing_candidate_prediction_excludes_hit_from_every_candidate(self) -> None:
        complete = BattleEncounterFitCandidate(
            candidate_ref="complete",
            predictions=(
                _hit("common", 100.0, 90.0),
                _hit("complete-only", 100.0, 100.0),
            ),
        )
        partial = BattleEncounterFitCandidate(
            candidate_ref="partial",
            predictions=(_hit("common", 100.0, 80.0),),
        )

        result = BattleEncounterFitService.select((complete, partial))
        complete_score = next(
            row for row in result.scores if row.candidate_ref == "complete"
        )

        self.assertEqual("complete", result.winner_ref)
        self.assertEqual(1, complete_score.used_hit_count)
        self.assertEqual(1, complete_score.excluded_hit_count)
        excluded = next(
            row for row in complete_score.hit_audits
            if row.event_id == "complete-only"
        )
        self.assertFalse(excluded.eligible)
        self.assertIn("并非所有候选都提供", excluded.exclusion_reason)

    def test_repeated_hits_share_one_group_and_do_not_claim_high_confidence(self) -> None:
        winner = BattleEncounterFitCandidate(
            candidate_ref="winner",
            predictions=tuple(
                _hit(f"repeat-{index}", 100.0, 100.0, group_id="same-dot")
                for index in range(20)
            ),
        )
        loser = BattleEncounterFitCandidate(
            candidate_ref="loser",
            predictions=tuple(
                _hit(f"repeat-{index}", 100.0, 70.0, group_id="same-dot")
                for index in range(20)
            ),
        )

        result = BattleEncounterFitService.select((loser, winner))
        winner_score = next(
            row for row in result.scores if row.candidate_ref == "winner"
        )

        self.assertEqual("winner", result.winner_ref)
        self.assertEqual(20, winner_score.used_hit_count)
        self.assertEqual(1, winner_score.used_group_count)
        self.assertEqual("低", result.confidence)

    def test_three_independent_groups_can_reach_medium_but_not_high(self) -> None:
        winner = BattleEncounterFitCandidate(
            candidate_ref="winner",
            predictions=tuple(
                _hit(f"hit-{index}", 100.0, 100.0, group_id=f"group-{index}")
                for index in range(3)
            ),
        )
        loser = BattleEncounterFitCandidate(
            candidate_ref="loser",
            predictions=tuple(
                _hit(f"hit-{index}", 100.0, 60.0, group_id=f"group-{index}")
                for index in range(3)
            ),
        )

        result = BattleEncounterFitService.select((winner, loser))

        self.assertEqual("winner", result.winner_ref)
        self.assertEqual("中", result.confidence)
        self.assertNotEqual("高", result.confidence)

    def test_equal_scores_keep_a_deterministic_low_confidence_default(self) -> None:
        alpha = BattleEncounterFitCandidate(
            candidate_ref="alpha",
            predictions=(_hit("hit", 100.0, 90.0),),
        )
        beta = BattleEncounterFitCandidate(
            candidate_ref="beta",
            predictions=(_hit("hit", 100.0, 90.0),),
        )

        first = BattleEncounterFitService.select((beta, alpha))
        second = BattleEncounterFitService.select((alpha, beta))

        self.assertEqual("alpha", first.winner_ref)
        self.assertEqual(first.winner_ref, second.winner_ref)
        self.assertEqual("ambiguous_default", first.selection_mode)
        self.assertEqual("低", first.confidence)
        self.assertEqual(0.0, first.score_gap)


if __name__ == "__main__":
    unittest.main()
