# 定义战报包导入导出的不可变领域值。

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


def portable_battle_evidence(value: Any, *, field: str = "") -> Any:
    """Copy portable evidence, dropping only external capture-file locations.

    Native hit fields and unknown future evidence remain intact. Raw JSON strings
    retain their exact representation unless a private file location is removed.
    """
    if isinstance(value, list):
        return [portable_battle_evidence(item) for item in value]
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if key in {"rawCapturePath", "raw_capture_path"} or (
                field == "rawCapture" and key == "path"
            ):
                result[key] = None
            else:
                result[key] = portable_battle_evidence(item, field=key)
        for raw_key, hash_key in (
            ("raw_summary_json", "raw_summary_sha256"),
            ("raw_record_json", "raw_record_sha256"),
        ):
            if hash_key in result and result.get(raw_key) != value.get(raw_key):
                result[hash_key] = hashlib.sha256(result[raw_key].encode("utf-8")).hexdigest()
        return result
    if isinstance(value, str) and field in {
        "raw_summary_json", "raw_record_json", "raw_hit_json", "payload_json",
    }:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return value  # DAO validation owns malformed original evidence.
        portable = portable_battle_evidence(decoded)
        if portable != decoded:
            return json.dumps(portable, ensure_ascii=False, separators=(",", ":"),
                              sort_keys=True, allow_nan=False)
    return value


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
