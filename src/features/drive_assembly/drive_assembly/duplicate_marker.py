# 标记驱动配装规划中的重复装备分组。
"""Mark duplicate equipment groups for drive assembly planning."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Callable


IGNORED_EQUIPMENT_FIELDS = {
    "uid",
    "display_name",
    "role_scores",
    "max_score",
    "is_mvp",
    "pick_order",
    "is_changed",
    "score",
}


def mark_duplicate_drive_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate drives that cannot be distinguished by the game filters."""

    return _mark_duplicate_items(
        blocks,
        kind="drive",
        signature_getter=_drive_block_signature,
    )


def mark_duplicate_tape_filters(filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate tapes that cannot be distinguished by the game filters."""

    return _mark_duplicate_items(
        filters,
        kind="tape",
        signature_getter=_tape_filter_signature,
    )


def equipment_signature(data: dict[str, Any]) -> str:
    """Return a stable JSON signature for equipment identity comparison."""

    normalized = _normalize_value(
        {
            key: value
            for key, value in (data or {}).items()
            if key not in IGNORED_EQUIPMENT_FIELDS and not key.startswith("duplicate_")
        }
    )
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _mark_duplicate_items(
    items: list[dict[str, Any]],
    *,
    kind: str,
    signature_getter: Callable[[dict[str, Any]], str],
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        signature = signature_getter(item)
        if signature:
            groups[signature].append(item)

    duplicate_group_number = 1
    for signature, group in groups.items():
        if len(group) < 2:
            continue
        group_id = f"{kind}_dup_{duplicate_group_number:03d}"
        duplicate_group_number += 1
        for index, item in enumerate(group, start=1):
            item["duplicate_group_id"] = group_id
            item["duplicate_index"] = index
            item["duplicate_count"] = len(group)
            item["duplicate_kind"] = kind
            item["equipment_signature"] = signature
            item["is_duplicate_equipment"] = True
            item[f"is_duplicate_{kind}"] = True
    return items


def _drive_block_signature(block: dict[str, Any]) -> str:
    drive = block.get("drive") if isinstance(block.get("drive"), dict) else {}
    if not drive:
        return ""
    data = {
        "item_type": "drive",
        "shape_id": str(block.get("drive_type") or drive.get("shape_id") or "").strip(),
        "quality": str(drive.get("quality") or "").strip(),
        "sub_stat_names": _sub_stat_names(drive.get("sub_stats")),
    }
    return equipment_signature(data)


def _tape_filter_signature(tape_filter: dict[str, Any]) -> str:
    tape = tape_filter.get("tape") if isinstance(tape_filter.get("tape"), dict) else {}
    data = {
        "item_type": "tape",
        "set_name": str(tape_filter.get("set_name") or tape.get("set_name") or "").strip(),
        "main_stat": str(
            tape_filter.get("main_stat") or _main_stat_name(tape.get("main_stats"))
        ).strip(),
        "sub_stat_names": _sub_stat_names(tape_filter.get("sub_stats") or tape.get("sub_stats")),
        "quality": str(tape_filter.get("quality") or tape.get("quality") or "").strip(),
    }
    return equipment_signature(data)


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _normalize_value(value[key])
            for key in sorted(value, key=lambda item: str(item))
            if key not in IGNORED_EQUIPMENT_FIELDS and not str(key).startswith("duplicate_")
        }
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_value(item) for item in value]
    return value


def _main_stat_name(main_stats: Any) -> str:
    if isinstance(main_stats, dict):
        return str(next(iter(main_stats.keys()), "")).strip()
    return str(main_stats or "").strip()


def _sub_stat_names(sub_stats: Any) -> list[str]:
    if isinstance(sub_stats, dict):
        names = sub_stats.keys()
    elif isinstance(sub_stats, list):
        names = sub_stats
    else:
        names = []
    return sorted(str(name).strip() for name in names if str(name).strip())
