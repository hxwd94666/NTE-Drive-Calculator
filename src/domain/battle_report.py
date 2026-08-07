# 定义战报服务与展示层共享的不可变领域值。
"""Immutable battle-report values shared by services and presentation code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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
    sequence: int = 0


@dataclass(frozen=True, slots=True)
class BattleCaptureState:
    phase: str
    message: str
    running: bool
    summary: BattleSummary | None = None
    error: str | None = None
    error_code: str | None = None
    persistence_status: str = "not_requested"
    battle_record_id: int | None = None
    retention_kind: Literal["auto", "manual"] | None = None


@dataclass(frozen=True, slots=True)
class BattleSummaryPersistenceOutcome:
    status: Literal["saved", "skipped_empty", "discarded_stale"]
    battle_record_id: int | None = None
    pruned_battle_record_ids: tuple[int, ...] = ()
    retention_kind: Literal["auto", "manual"] | None = None


@dataclass(frozen=True, slots=True)
class StoredBattleSummary:
    battle_record_id: int
    retention_kind: Literal["auto", "manual"]
    saved_at_utc: str
    detail_scope: Literal["current", "first", "second"]
    summary: BattleSummary


@dataclass(frozen=True, slots=True)
class BattleReportHistoryEntry:
    battle_record_id: int
    retention_kind: Literal["auto", "manual"]
    saved_at_utc: str
    combat_context_kind: Literal["abyss", "non_abyss"]
    abyss_floor: int | None
    has_first_half: bool
    has_second_half: bool
    character_ids: tuple[int, ...]
    total_damage: float
    total_dps: float
    duration_seconds: float
    total_hits: int
    capability_level: str
    source_kind: str


@dataclass(frozen=True, slots=True)
class BattleRetentionMutation:
    battle_record_id: int
    retention_kind: Literal["auto", "manual"]
    changed: bool
    pruned_battle_record_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class DamageCompositionEntry:
    key: str
    label: str
    damage: float
    share_percent: float


@dataclass(frozen=True, slots=True)
class RoleDamageComposition:
    character_id: int
    character_name: str
    total_damage: float
    entries: tuple[DamageCompositionEntry, ...]


@dataclass(frozen=True, slots=True)
class BattleDamageComposition:
    roles: tuple[RoleDamageComposition, ...]
    other_total_damage: float
    other_share_percent: float
    other_entries: tuple[DamageCompositionEntry, ...]


EMPTY_BATTLE_CAPTURE_STATE = BattleCaptureState(
    phase="stopped",
    message="尚未开始战报采集。",
    running=False,
)


def active_abyss_half(summary: BattleSummary) -> BattleAbyssHalfSummary | None:
    """Return the currently active half without making UI code parse labels."""

    active = (summary.abyss.active_half or "").lower()
    if "ascending" in active or "first" in active or "上" in active:
        return summary.abyss.first_half
    if "descending" in active or "second" in active or "下" in active:
        return summary.abyss.second_half
    return None
