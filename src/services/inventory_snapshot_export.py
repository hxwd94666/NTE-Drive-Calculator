# 从 SQLite 导出指定的不可变背包快照给外部工具。
"""Export a selected immutable SQLite inventory snapshot for external tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.storage.sqlite.user_data_dao import UserDataDao


def export_inventory_snapshot(
    database_path: str | Path,
    snapshot_id: int | None = None,
) -> dict[str, Any]:
    """Return official inventory fields from one complete SQLite snapshot.

    JSON is an optional caller-owned transport format only; this service never
    reads or writes a mutable inventory JSON file.
    """

    with UserDataDao(database_path) as user_dao:
        selected_id = user_dao.current_inventory_snapshot_id() if snapshot_id is None else int(snapshot_id)
        if selected_id is None:
            raise ValueError("尚无可导出的稳定背包快照")
        summary = user_dao.inventory_snapshot_summary(selected_id)
        if summary is None or not summary.get("complete"):
            raise ValueError(f"背包快照 {selected_id} 不存在或未完成")
        return {
            "snapshot_id": selected_id,
            "source": summary["source"],
            "captured_at_utc": summary["captured_at_utc"],
            "items": user_dao.list_inventory_items(selected_id),
        }
