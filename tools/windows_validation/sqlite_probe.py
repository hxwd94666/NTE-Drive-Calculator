# 以只读连接采集账号数据库 schema、稳定快照和装配任务摘要。
"""Read-only SQLite evidence probes."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    return int(row[0]) if row else 0


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def sqlite_summary(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"configured": False}
    resolved = path.resolve()
    if not resolved.is_file():
        return {"configured": True, "exists": False, "path": str(resolved)}
    uri = f"{resolved.as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        tables = _table_names(connection)
        summary: dict[str, Any] = {
            "configured": True,
            "exists": True,
            "path": str(resolved),
            "schema_version": connection.execute("PRAGMA user_version").fetchone()[0],
            "table_count": len(tables),
        }
        for table in (
            "inventory_snapshot",
            "equipment_apply_job",
            "equipment_apply_job_item",
        ):
            if table in tables:
                summary[f"{table}_count"] = _count(connection, table)
        snapshot_columns = (
            _column_names(connection, "inventory_snapshot")
            if "inventory_snapshot" in tables
            else set()
        )
        required = {
            "snapshot_id",
            "source",
            "complete",
            "declared_item_count",
            "stored_item_count",
            "raw_snapshot_json",
            "is_current",
        }
        if required <= snapshot_columns:
            row = connection.execute(
                """SELECT snapshot_id, source, complete, declared_item_count,
                          stored_item_count, raw_snapshot_json
                   FROM inventory_snapshot
                   WHERE is_current = 1
                   ORDER BY snapshot_id DESC LIMIT 1"""
            ).fetchone()
            if row is not None:
                try:
                    raw = json.loads(str(row[5] or "{}"))
                except json.JSONDecodeError:
                    raw = {}
                summary["current_inventory"] = {
                    "snapshot_id": int(row[0]),
                    "source": str(row[1]),
                    "complete": bool(row[2]),
                    "declared_item_count": int(row[3]),
                    "stored_item_count": int(row[4]),
                    "capture_driver": str(raw.get("capture_driver") or ""),
                }
        return summary
    finally:
        connection.close()
