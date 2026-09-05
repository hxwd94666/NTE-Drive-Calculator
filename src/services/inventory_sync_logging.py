# 将背包快照投影为仅含计数与版本的安全日志字段。
"""Count-only inventory summaries shared by synchronization log events."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

from src.observability import OperationContext, log_event

from .inventory_snapshot_stabilizer import SnapshotOfferResult


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _list_state(payload: Mapping[str, Any], field: str) -> str:
    if field not in payload:
        return "missing"
    return "list" if isinstance(payload[field], list) else "invalid"


def inventory_core_log_fields(hello: Mapping[str, Any]) -> dict[str, str]:
    """只记录符合版本格式的握手标量，不透传任意字符串。"""
    fields: dict[str, str] = {}
    for key in ("core_version", "data_version"):
        value = hello.get(key)
        if (
            isinstance(value, str) and len(value) <= 32
            and re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", value)
        ):
            fields[key] = value
    return fields


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
        "complete": payload.get("complete") if isinstance(payload.get("complete"), bool) else None,
        "items_field": _list_state(payload, "items"),
        "characters_field": _list_state(payload, "characters"),
        "declared_item_count": _count(payload.get("item_count")),
        "declared_character_count": _count(payload.get("character_count")),
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
        "generation": _count(payload.get("generation")),
        "sequence": _count(payload.get("sequence")),
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


class InventorySyncDiagnostics:
    """在同步工作线程内汇总已取出的事件；重复判定限频，不保存原始数据。"""

    def __init__(self, context: OperationContext) -> None:
        self.context = context
        self.started_at = time.monotonic()
        self.last_summary_at = self.started_at
        self.last_event_at: float | None = None
        self.last_logged_at: dict[tuple[str, str | None], float] = {}
        self.counts = dict.fromkeys(
            ("collecting", "changed", "duplicate", "unchanged", "reverted", "ignored"), 0,
        )
        self.reason_counts: dict[str, int] = {}
        self.committed_count = 0
        self.save_failure_count = 0

    def record(
        self, event: Mapping[str, Any], result: SnapshotOfferResult,
        *, guard_item_count: int | None,
    ) -> None:
        now = time.monotonic()
        self.last_event_at = now
        self.counts[result.status] += 1
        if result.reason_code is not None:
            self.reason_counts[result.reason_code] = self.reason_counts.get(result.reason_code, 0) + 1
        key = (result.status, result.reason_code)
        previous = self.last_logged_at.get(key)
        if previous is not None and now - previous < 30.0:
            return
        self.last_logged_at[key] = now
        log_event(
            "INFO", "inventory_sync.event_evaluated", "背包事件已判定", self.context,
            outcome=result.status, reason_code=result.reason_code,
            processed_event_count=sum(self.counts.values()),
            outcome_count=self.counts[result.status],
            guard_item_count=guard_item_count,
            added_count=result.added_count, removed_count=result.removed_count,
            **inventory_payload_log_fields(event),
        )

    def summary(
        self, *, phase: str, pending_item_count: int | None,
        snapshot_id: int | None, final: bool = False,
    ) -> None:
        now = time.monotonic()
        if not final and now - self.last_summary_at < 30.0:
            return
        self.last_summary_at = now
        log_event(
            "INFO", "inventory_sync.session_summary", "背包同步会话处理摘要", self.context,
            phase=phase, final=final, snapshot_id=snapshot_id,
            duration_seconds=round(now - self.started_at, 1),
            processed_event_count=sum(self.counts.values()),
            outcome_counts=dict(self.counts), reason_counts=dict(self.reason_counts),
            committed_count=self.committed_count, save_failure_count=self.save_failure_count,
            pending_item_count=pending_item_count,
            seconds_since_last_processed_event=(
                round(now - self.last_event_at, 1) if self.last_event_at is not None else None
            ),
        )
