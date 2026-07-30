# 将背包快照投影为仅含计数与版本的安全日志字段。
"""Count-only inventory summaries shared by synchronization log events."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def inventory_payload_log_fields(message: Mapping[str, Any]) -> dict[str, object]:
    """Summarize a candidate event without logging any inventory row or UID."""

    payload = (
        message.get("params")
        if message.get("method") == "event.inventory.snapshot"
        else message
    )
    if not isinstance(payload, Mapping):
        return {}
    raw_items = payload.get("items")
    items = raw_items if isinstance(raw_items, list) else []
    character_rows = payload.get("characters")
    return {
        "item_count": len(items),
        "module_count": sum(
            1
            for item in items
            if isinstance(item, Mapping) and item.get("kind") == "module"
        ),
        "core_count": sum(
            1
            for item in items
            if isinstance(item, Mapping) and item.get("kind") == "core"
        ),
        "equipped_count": sum(
            1
            for item in items
            if isinstance(item, Mapping) and item.get("equipped") is True
        ),
        "locked_count": sum(
            1
            for item in items
            if isinstance(item, Mapping) and item.get("locked") is True
        ),
        "character_instance_count": (
            len(character_rows) if isinstance(character_rows, list) else 0
        ),
        "character_instances_independent": isinstance(character_rows, list),
        "generation": payload.get("generation"),
        "sequence": payload.get("sequence"),
    }


def stored_snapshot_log_fields(
    summary: Mapping[str, Any],
    *,
    character_instances_independent: bool,
) -> dict[str, object]:
    """Project an authoritative SQLite summary without inventory details."""

    return {
        "item_count": int(summary.get("stored_item_count") or 0),
        "module_count": int(summary.get("module_count") or 0),
        "core_count": int(summary.get("core_count") or 0),
        "equipped_count": int(summary.get("equipped_count") or 0),
        "locked_count": int(summary.get("locked_count") or 0),
        "character_instance_count": int(summary.get("character_instance_count") or 0),
        "character_instances_independent": character_instances_independent,
        "generation": summary.get("generation"),
        "sequence": summary.get("sequence"),
        "source": summary.get("source"),
    }
