# 为固定轴反事实选择同一逐击隐状态下的原始/候选公式值。
"""Branch-stable formula pairs for observed-hit counterfactual ratios."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from src.domain.battle_report import BattleHitReplayResult


@dataclass(frozen=True, slots=True)
class PairedReplayFormula:
    """Two formula values evaluated on the original hit's hidden branch."""

    baseline_damage: float
    candidate_damage: float
    method: str
    explanation: str


def _positive(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if number > 0.0 and isfinite(number) else None


def _candidate_critical_value(
    replay: BattleHitReplayResult,
) -> float | None:
    critical = _positive(replay.critical_damage)
    if critical is not None:
        return critical
    if replay.critical_policy == "disabled":
        return _positive(replay.non_critical_damage)
    return None


def paired_replay_formula(
    baseline: BattleHitReplayResult | None,
    candidate: BattleHitReplayResult | None,
) -> PairedReplayFormula | None:
    """Keep the baseline hit's branch while comparing two replayed builds.

    A candidate replay is evaluated against the same observed hit, so its own
    nearest-branch classification is not independent evidence and must not
    switch the counterfactual branch. Ambiguous hits use expected values only
    when the damage channel's critical policy is known.
    """

    if baseline is None or candidate is None:
        return None
    state = baseline.critical_state
    if state == "critical":
        original_value = _positive(baseline.critical_damage)
        candidate_value = _candidate_critical_value(candidate)
        explanation = "沿用原始逐击已识别的暴击分支"
        method = "structured_selected"
    elif state in {"non_critical", "not_applicable"}:
        original_value = _positive(baseline.non_critical_damage)
        candidate_value = _positive(candidate.non_critical_damage)
        explanation = (
            "沿用正式不可暴击分支"
            if state == "not_applicable"
            else "沿用原始逐击已识别的非暴击分支"
        )
        method = "structured_selected"
    elif state == "ambiguous":
        if (
            baseline.critical_policy == "unknown"
            or candidate.critical_policy == "unknown"
        ):
            return None
        original_value = _positive(baseline.expected_damage)
        candidate_value = _positive(candidate.expected_damage)
        explanation = "暴击分支未唯一识别，使用同一正式暴击策略的期望公式"
        method = "structured_expected"
    else:
        return None
    if original_value is None or candidate_value is None:
        return None
    return PairedReplayFormula(
        baseline_damage=original_value,
        candidate_damage=candidate_value,
        method=method,
        explanation=explanation,
    )


def replay_formula_value(
    replay: BattleHitReplayResult | None,
) -> tuple[float | None, str]:
    """Return one replay's branch-stable display value and method."""

    pair = paired_replay_formula(replay, replay)
    if pair is None:
        return None, ""
    return pair.baseline_damage, pair.method
