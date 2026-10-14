# 保存采集摘要与活动半场观测，不借用另一半场或推断未观测伤害。
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BattleCharacterSummary:
    character_id: int
    name: str
    hits: int
    damage: float
    dps: float
    damage_share_percent: float
    hits_taken: int = 0
    damage_taken: float = 0.0


@dataclass(frozen=True, slots=True)
class BattleSkillSummary:
    character_id: int
    character_name: str
    name: str
    category: str
    hits: int
    damage: float
    damage_share_percent: float
    ability_name: str | None = None
    gameplay_effect_name: str | None = None
    is_follow_up: bool = False


@dataclass(frozen=True, slots=True)
class BattleAbyssHalfSummary:
    half: str
    duration_seconds: float
    total_damage: float
    total_dps: float
    characters: tuple[BattleCharacterSummary, ...]
    skills: tuple[BattleSkillSummary, ...]


@dataclass(frozen=True, slots=True)
class BattleAbyssSummary:
    detected: bool = False
    floor: int | None = None
    active_half: str | None = None
    success: bool = False
    first_half: BattleAbyssHalfSummary | None = None
    second_half: BattleAbyssHalfSummary | None = None


@dataclass(frozen=True, slots=True)
class BattleQualitySummary:
    source: str = "unknown"
    packet_count: int = 0
    packets_with_hits: int = 0
    hit_count: int = 0
    outgoing_hits: int = 0
    incoming_hits: int = 0
    unknown_direction_hits: int = 0
    unknown_character_count: int = 0
    unknown_character_hits: int = 0
    unmapped_skill_rows: int = 0
    unmapped_skill_hits: int = 0
    unmapped_gameplay_effect_count: int = 0


@dataclass(frozen=True, slots=True)
class BattleSummary:
    duration_seconds: float
    dps_time_mode: str
    total_damage: float
    total_dps: float
    total_damage_taken: float
    total_hits: int
    characters: tuple[BattleCharacterSummary, ...]
    skills: tuple[BattleSkillSummary, ...]
    abyss: BattleAbyssSummary
    quality: BattleQualitySummary
    max_hp_reduction: float = 0.0
    sequence: int = 0


def active_abyss_half(summary: BattleSummary) -> BattleAbyssHalfSummary | None:
    """只定位摘要明确标记的活动半场，未确认时返回 None，不借另一半场。"""

    active = (summary.abyss.active_half or "").lower()
    if "ascending" in active or "first" in active or "上" in active:
        return summary.abyss.first_half
    if "descending" in active or "second" in active or "下" in active:
        return summary.abyss.second_half
    return None


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
    # 已检测到深渊但活动半场未知时保持等待；全场累计可能属于上一半场，不能充当当前观测。
    if summary.abyss.detected:
        half = active_abyss_half(summary)
        return half is not None and (half.total_damage > 0 or _has_rows(half.characters, half.skills))
    return has_battle_observation(summary)
