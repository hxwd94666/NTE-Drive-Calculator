# 以只读连接采集账号数据库 schema、稳定快照和装配任务摘要。
"""Read-only SQLite evidence probes."""

from __future__ import annotations

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
        return summary
    finally:
        connection.close()

