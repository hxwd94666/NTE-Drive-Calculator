# 验证固定轴反事实沿用原击隐状态，不让候选公式重新选择暴击分支。
from __future__ import annotations

import unittest

from src.domain.battle_report import BattleHitReplayResult
from src.services.battle_replay_formula_ratio_service import (
    paired_replay_formula,
)


def _replay(
    *,
    noncritical: float,
    critical: float,
    expected: float,
    state: str,
    policy: str = "character",
) -> BattleHitReplayResult:
    return BattleHitReplayResult(
        event_id="hit:1",
        observed_damage=200.0,
        non_critical_damage=noncritical,
        critical_damage=critical,
        selected_damage=critical if state == "critical" else noncritical,
        selected_error_percent=0.0,
        critical_state=state,
        confidence="中",
        factors=(),
        critical_rate=0.5,
        expected_damage=expected,
        corrected_expected_damage=expected,
        critical_policy=policy,
    )


class BattleReplayFormulaRatioServiceTests(unittest.TestCase):
    def test_resolved_critical_hit_keeps_original_critical_branch(self) -> None:
        original = _replay(
            noncritical=100.0,
            critical=200.0,
            expected=150.0,
            state="critical",
        )
        candidate = _replay(
            noncritical=120.0,
            critical=400.0,
            expected=260.0,
            state="non_critical",
        )

        pair = paired_replay_formula(original, candidate)

        self.assertIsNotNone(pair)
        assert pair is not None
        self.assertEqual("structured_selected", pair.method)
        self.assertEqual(200.0, pair.baseline_damage)
        self.assertEqual(400.0, pair.candidate_damage)

    def test_ambiguous_hit_uses_expected_formula_without_nearest_branch(self) -> None:
        original = _replay(
            noncritical=100.0,
            critical=200.0,
            expected=150.0,
            state="ambiguous",
        )
        candidate = _replay(
            noncritical=120.0,
            critical=240.0,
            expected=180.0,
            state="ambiguous",
        )

        pair = paired_replay_formula(original, candidate)

        self.assertIsNotNone(pair)
        assert pair is not None
        self.assertEqual("structured_expected", pair.method)
        self.assertEqual(150.0, pair.baseline_damage)
        self.assertEqual(180.0, pair.candidate_damage)

    def test_unknown_crit_policy_stays_unquantified(self) -> None:
        original = _replay(
            noncritical=100.0,
            critical=200.0,
            expected=150.0,
            state="ambiguous",
            policy="unknown",
        )
        candidate = _replay(
            noncritical=120.0,
            critical=240.0,
            expected=180.0,
            state="ambiguous",
            policy="unknown",
        )

        self.assertIsNone(paired_replay_formula(original, candidate))


if __name__ == "__main__":
    unittest.main()
