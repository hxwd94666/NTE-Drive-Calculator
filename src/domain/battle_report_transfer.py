# 定义战报包导入导出的不可变领域值。

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BattleReportTransferEntry:
    battle_record_id: int
    captured_at_utc: str
    gameplay_label: str
    scope_label: str
    completeness_label: str
    cursor_label: str
    retention_label: str
    total_hits: int


@dataclass(frozen=True, slots=True)
class BattleReportExportOutcome:
    report_count: int
    byte_count: int


@dataclass(frozen=True, slots=True)
class BattleReportImportOutcome:
    imported_record_ids: tuple[int, ...]
    skipped_existing_count: int
