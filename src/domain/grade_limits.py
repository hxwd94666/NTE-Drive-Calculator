# 装备等级门槛常量与判断（无 scoring 依赖）。
"""Grade ladder thresholds shared by scoring, strategies, and UI."""

from __future__ import annotations

GRADE_LADDER: tuple[str, ...] = ("D", "C", "B", "A", "S", "SS", "SSS", "ACE")
STAT_PRIORITY_GRADE_OPTIONS: tuple[str, ...] = GRADE_LADDER

GRADE_MIN_RATIOS: dict[str, float] = {
    "D": 0.0,
    "C": 0.2,
    "B": 0.3,
    "A": 0.4,
    "S": 0.5,
    "SS": 0.6,
    "SSS": 0.7,
    "ACE": 0.8,
}


def meets_min_grade(score: float, area: int, min_grade: str) -> bool:
    normalized = str(min_grade or "A").upper()
    ratio_threshold = GRADE_MIN_RATIOS.get(normalized, GRADE_MIN_RATIOS["A"])
    max_score = float(area or 0) * 10.0
    if max_score <= 0:
        return normalized == "D"
    return float(score or 0.0) / max_score >= ratio_threshold
