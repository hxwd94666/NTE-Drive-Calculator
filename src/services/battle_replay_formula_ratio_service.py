# 为固定轴反事实选择原始与候选逐击的正式暴击期望公式。
"""Expected formula pairs weighted by the original observed hit damage."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from src.domain.battle_report import BattleHitReplayResult


@dataclass(frozen=True, slots=True)
class PairedReplayFormula:
    """Two expected formula values evaluated under each build's formal policy."""

    baseline_damage: float
    candidate_damage: float
    method: str
    explanation: str


def _positive(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if number > 0.0 and isfinite(number) else None


def _expected_value(
    replay: BattleHitReplayResult,
) -> float | None:
    if replay.critical_state == "unreplayable":
        return None
    if replay.critical_policy == "disabled":
        return _positive(replay.non_critical_damage)
    if replay.critical_policy == "fixed" and replay.critical_rate is None:
        return None
    if replay.critical_policy in {"character", "fixed"}:
        return _positive(replay.expected_damage)
    return None


def paired_replay_formula(
    baseline: BattleHitReplayResult | None,
    candidate: BattleHitReplayResult | None,
) -> PairedReplayFormula | None:
    """Compare expectations without freezing the observed critical outcome.

    The caller weights this ratio by the original observed damage. Each replay
    already includes that hit's Buffs, capped rate and formal critical policy.
    Neither selected branches nor residual-corrected values enter the ratio.
    """

    if baseline is None or candidate is None:
        return None
    original_value = _expected_value(baseline)
    candidate_value = _expected_value(candidate)
    if original_value is None or candidate_value is None:
        return None
    disabled = baseline.critical_policy == candidate.critical_policy == "disabled"
    return PairedReplayFormula(
        baseline_damage=original_value,
        candidate_damage=candidate_value,
        method="structured_selected" if disabled else "structured_expected",
        explanation=(
            "双方使用正式不可暴击公式"
            if disabled else "按每击正式暴击策略比较理论期望，以原始逐击伤害加权"
        ),
    )


def replay_formula_value(
    replay: BattleHitReplayResult | None,
) -> tuple[float | None, str]:
    """Return the formula value displayed for an expected counterfactual."""

    pair = paired_replay_formula(replay, replay)
    if pair is None:
        return None, ""
    return pair.baseline_damage, pair.method
