# 保存账号战报历史和留存结果，记录身份与冻结来源不随页面变化。
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from src.domain.battle_report.capture.summary import BattleSummary


@dataclass(frozen=True, slots=True)
class BattleSummaryPersistenceOutcome:
    status: Literal["saved", "skipped_empty", "discarded_stale"]
    battle_record_id: int | None = None
    pruned_battle_record_ids: tuple[int, ...] = ()
    retention_kind: Literal["auto", "manual"] | None = None
    warning_message: str | None = None


@dataclass(frozen=True, slots=True)
class StoredBattleSummary:
    battle_record_id: int
    retention_kind: Literal["auto", "manual"]
    saved_at_utc: str
    detail_scope: Literal["current", "first", "second"]
    summary: BattleSummary
    nte_core_version: str | None = None
    nte_core_protocol_version: int | None = None
    nte_core_data_version: str | None = None
    nte_core_executable_sha256: str | None = None
    analysis_start_us: int | None = None
    analysis_end_us: int | None = None
    analysis_character_id: int | None = None


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
    environment_name: str = ""
    environment_source: Literal["", "user_confirmed", "inferred"] = ""
    environment_confidence: str = ""
    native_capture: bool = False


@dataclass(frozen=True, slots=True)
class BattleRetentionMutation:
    battle_record_id: int
    retention_kind: Literal["auto", "manual"]
    changed: bool
    pruned_battle_record_ids: tuple[int, ...] = ()
