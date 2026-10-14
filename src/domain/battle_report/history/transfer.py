# 校验战报包的可移植证据与装备指纹，不改写原始逐击含义。
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from typing import Any
import hashlib
import json


def portable_battle_evidence(value: Any, *, field: str = "") -> Any:
    """复制可移植证据，只去除外部抓包文件位置。

    原生逐击字段与未来未知证据完整保留；未去除文件位置时，
    原始 JSON 字符串保持原表示。
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
            return value  # 损坏原始证据的拒绝由 DAO 校验负责，此处不重新解释。
        portable = portable_battle_evidence(decoded)
        if portable != decoded:
            return json.dumps(portable, ensure_ascii=False, separators=(",", ":"),
                              sort_keys=True, allow_nan=False)
    return value


def canonical_battle_equipment_json(
    equipment: Sequence[Mapping[str, Any]],
) -> str:
    """以无外部指针的规范 JSON 建立导入装备锁指纹，保留输入顺序与字段。"""

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
