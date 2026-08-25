# 定义战报包导入导出的不可变领域值。

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


def canonical_battle_equipment_json(
    equipment: Sequence[Mapping[str, Any]],
) -> str:
    """Return the pointer-free canonical payload used by import equipment locks."""

    if isinstance(equipment, (str, bytes)) or not isinstance(equipment, Sequence):
        raise ValueError("equipment must be a sequence")
    rows = []
    for item in equipment:
        if not isinstance(item, Mapping):
            raise ValueError("equipment item must be an object")
        rows.append(dict(item))
    return json.dumps(
        rows,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def battle_equipment_sha256(equipment: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        canonical_battle_equipment_json(equipment).encode("utf-8")
    ).hexdigest()


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
