# 区分采集空摘要与已观测命中，活动半场不得借用另一半场的数据。
from src.domain.battle_report import BattleSummary, active_abyss_half


def has_battle_observation(summary: BattleSummary) -> bool:
    quality = summary.quality
    return (summary.total_hits > 0 or summary.total_damage > 0 or summary.total_damage_taken > 0
            or quality.hit_count > 0 or quality.outgoing_hits > 0 or quality.incoming_hits > 0
            or quality.unknown_direction_hits > 0
            or _has_rows(summary.characters, summary.skills))


def _has_rows(characters, skills) -> bool:
    return (any(row.hits > 0 or row.hits_taken > 0 or row.damage > 0 or row.damage_taken > 0
                for row in characters)
            or any(row.hits > 0 or row.damage > 0 for row in skills))


def has_active_battle_observation(summary: BattleSummary) -> bool:
    if summary.abyss.detected and summary.abyss.active_half:
        half = active_abyss_half(summary)
        return half is not None and (half.total_damage > 0 or _has_rows(half.characters, half.skills))
    return has_battle_observation(summary)
