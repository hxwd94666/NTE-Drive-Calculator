# 校验完整视觉扫描结果并原子写入账号库存快照。
"""Completion gate shared by mouse and virtual-gamepad full visual scans."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from src.services.vision_inventory_snapshot import import_vision_inventory


class IncompleteVisionScanError(RuntimeError):
    def __init__(self, *, expected_count: int, parsed_count: int, failed_count: int) -> None:
        super().__init__(
            f"预计 {expected_count} 件，完整解析 {parsed_count} 件，失败 {failed_count} 件。"
        )
        self.expected_count = expected_count
        self.parsed_count = parsed_count
        self.failed_count = failed_count


def _unknown_tape_main_numbers(items: Sequence[Mapping[str, Any]]) -> tuple[int, ...]:
    numbers: list[int] = []
    for fallback_number, item in enumerate(items, start=1):
        if str(item.get("item_type") or "") != "tape":
            continue
        if str(item.get("main_stats") or "").strip() != "未知主词条":
            continue
        try:
            number = int(item.get("_scan_number") or fallback_number)
        except (TypeError, ValueError):
            number = fallback_number
        if number not in numbers:
            numbers.append(number)
    return tuple(sorted(numbers))


def append_tape_main_warning(summary: str, items: Sequence[Mapping[str, Any]]) -> str:
    """Append one bounded warning only when a scanned card main stat is unknown."""

    numbers = _unknown_tape_main_numbers(items)
    if not numbers:
        return summary
    listed = "、".join(f"{number}号" for number in numbers[:2])
    suffix = "……" if len(numbers) > 2 else ""
    return f"{summary}\n卡带主词条解析失败：{len(numbers)}个（{listed}{suffix}）。"


def commit_completed_vision_inventory(
    database_path: str | Path,
    stats: Mapping[str, Any],
    manual_items: Sequence[Mapping[str, Any]],
) -> int | None:
    vision_items = list(stats.get("vision_items") or [])
    if not vision_items or str(stats.get("parse_scope") or "") not in {"full", "all"}:
        return None
    complete_items = [*vision_items, *manual_items]
    expected_count = int(stats.get("total_count", 0) or 0)
    failed_count = int(stats.get("failed_count", 0) or 0)
    if failed_count or (expected_count and len(complete_items) != expected_count):
        raise IncompleteVisionScanError(
            expected_count=expected_count,
            parsed_count=len(complete_items),
            failed_count=failed_count,
        )
    return import_vision_inventory(
        database_path,
        complete_items,
        capture_driver=str(stats.get("capture_driver") or "mouse"),
    )
