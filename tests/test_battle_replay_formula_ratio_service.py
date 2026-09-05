# 验证固定轴反事实统一比较理论期望，不受已识别暴击结果影响。
from __future__ import annotations

import unittest
from dataclasses import replace

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
    def test_resolved_critical_hit_uses_expected_ratio(self) -> None:
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
        self.assertEqual("structured_expected", pair.method)
        self.assertEqual(150.0, pair.baseline_damage)
        self.assertEqual(260.0, pair.candidate_damage)

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

    def test_rate_gain_ignores_both_branch_labels_and_corrected_values(self) -> None:
        original = _replay(noncritical=100.0, critical=300.0, expected=200.0, state="critical")
        candidate = replace(original, critical_rate=0.6, expected_damage=220.0)
        for state in ("critical", "non_critical", "ambiguous"):
            with self.subTest(state=state):
                pair = paired_replay_formula(
                    replace(original, critical_state=state, corrected_expected_damage=9999.0),
                    replace(candidate, critical_state="non_critical", corrected_expected_damage=1.0),
                )
                assert pair is not None
                self.assertAlmostEqual(1.1, pair.candidate_damage / pair.baseline_damage)

    def test_resolved_label_does_not_replace_missing_policy_or_formula(self) -> None:
        original = _replay(noncritical=100.0, critical=200.0, expected=150.0, state="critical")
        for incomplete in (
            replace(original, critical_policy="unknown"),
            replace(original, critical_policy="fixed", critical_rate=None),
            replace(original, expected_damage=None),
            replace(original, critical_state="unreplayable"),
        ):
            self.assertIsNone(paired_replay_formula(original, incomplete))
            self.assertIsNone(paired_replay_formula(incomplete, original))

    def test_disabled_policy_uses_noncritical_formula(self) -> None:
        original = _replay(
            noncritical=100.0, critical=200.0, expected=150.0,
            state="not_applicable", policy="disabled",
        )
        pair = paired_replay_formula(original, replace(original, non_critical_damage=120.0))
        assert pair is not None
        self.assertEqual("structured_selected", pair.method)
        self.assertAlmostEqual(1.2, pair.candidate_damage / pair.baseline_damage)


if __name__ == "__main__":
    unittest.main()
