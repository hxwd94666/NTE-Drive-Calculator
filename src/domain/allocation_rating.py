# 定义单件装备与完整配装方案各自独立的纯评级规则。
"""Pure score-to-grade rules shared by equipment and loadout presenters."""

from __future__ import annotations


def allocation_grade(score: float, area: float) -> str:
    """单件按面积满分换算评级；完整方案累计分另走固定区间。"""

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


def loadout_total_grade(score: float) -> str:
    """只按完整方案累计分的固定区间评级，不套用单件面积阈值。"""

    value = float(score)
    if value >= 280:
        return "ACE"
    if value >= 260:
        return "SSS"
    if value >= 240:
        return "SS"
    if value >= 220:
        return "S"
    if value >= 200:
        return "A"
    if value >= 180:
        return "B"
    if value >= 160:
        return "C"
    return "D"
