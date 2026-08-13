# 统一读取和归一化配装方案中的装备评分。
"""Pure score helpers for persisted loadout assignments."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def assignment_score_key(assignment: Mapping[str, Any]) -> str:
    """Return the persisted score key for one normalized assignment."""

    return (
        f"nte-{assignment.get('kind')}-"
        f"{int(assignment.get('uid_slot') or 0)}-"
        f"{int(assignment.get('uid_serial') or 0)}"
    )


def exact_assignment_score_total(
    assignments: Sequence[Mapping[str, Any]],
    assignment_scores: Mapping[str, Any],
) -> float | None:
    """Sum every concrete slot when the persisted per-item scores are complete."""

    keys = tuple(assignment_score_key(assignment) for assignment in assignments)
    if any(key not in assignment_scores for key in keys):
        return None
    return round(sum(float(assignment_scores[key]) for key in keys), 6)
