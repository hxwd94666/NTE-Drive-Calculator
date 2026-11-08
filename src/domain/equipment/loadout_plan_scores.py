# 统一读取和归一化配装方案中的装备评分。
"""Pure score helpers for persisted loadout assignments."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any
import math


def assignment_score_key(assignment: Mapping[str, Any]) -> str:
    """Return the persisted score key for one normalized assignment."""

    return (
        f"nte-{assignment.get('kind')}-"
        f"{int(assignment.get('uid_slot') or 0)}-"
        f"{int(assignment.get('uid_serial') or 0)}"
    )


def accumulate_assignment_scores(values: Iterable[Any]) -> float:
    """Accumulate frozen per-item scores once with the shared precision."""

    return round(sum(float(value) for value in values), 6)


def exact_assignment_score_total(
    assignments: Sequence[Mapping[str, Any]],
    assignment_scores: Mapping[str, Any],
) -> float | None:
    """Sum every concrete slot when the persisted per-item scores are complete."""

    keys = tuple(assignment_score_key(assignment) for assignment in assignments)
    if any(key not in assignment_scores for key in keys):
        return None
    return accumulate_assignment_scores(assignment_scores[key] for key in keys)


def persisted_loadout_total_score(plan: Mapping[str, Any]) -> float | None:
    """优先展示完整逐件冻结分之和，旧方案才使用保存总分；不读取当前权重。"""
    payload = plan.get("payload") or {}
    scores = payload.get("assignment_scores") or {}
    value = plan.get("score")
    if "assignments" in plan:
        exact = exact_assignment_score_total(plan["assignments"], scores)
        if exact is not None:
            value = exact
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)
