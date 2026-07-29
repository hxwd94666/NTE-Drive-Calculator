# 定义配装分数相对装备面积的纯评级规则。
"""Pure allocation score-to-grade rules shared by result presenters."""

from __future__ import annotations


def allocation_grade(score: float, area: float) -> str:
    """Return the display grade for a score and its maximum scoring area."""

    maximum = float(area) * 10.0
    if maximum <= 0:
        return "D"
    ratio = float(score) / maximum
    if ratio >= 0.8:
        return "ACE"
    if ratio >= 0.7:
        return "SSS"
    if ratio >= 0.6:
        return "SS"
    if ratio >= 0.5:
        return "S"
    if ratio >= 0.4:
        return "A"
    if ratio >= 0.3:
        return "B"
    if ratio >= 0.2:
        return "C"
    return "D"
