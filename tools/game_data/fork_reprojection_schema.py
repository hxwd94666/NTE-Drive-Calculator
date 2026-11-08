# 弧盘定向重投影只补齐必需的审查结构，不冒称无关 schema 已升级。
from datetime import datetime, timezone
import sqlite3

from tools.game_data.static_database_build_support import SCHEMA_PATHS, SCHEMA_VERSION

REVIEW_SCHEMA_VERSION = 39


def reprojection_target_schema(source_version: int) -> int:
    if not 38 <= source_version <= SCHEMA_VERSION:
        raise ValueError(f"候选静态库 schema 不是可升级版本：{source_version}")
    return max(REVIEW_SCHEMA_VERSION, source_version)


def ensure_reprojection_schema(connection: sqlite3.Connection) -> tuple[int, int]:
    source_version = int(connection.execute("SELECT COALESCE(MAX(version),0) FROM schema_migration").fetchone()[0])
    target_version = reprojection_target_schema(source_version)
    if target_version != source_version:
        # canonical 39 是唯一缺失依赖；版本记录只能在对应 SQL 完整成功后追加。
        script = next(path for path in SCHEMA_PATHS if int(path.name.split("_", 1)[0]) == REVIEW_SCHEMA_VERSION)
        connection.executescript(script.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO schema_migration VALUES (?,?)",
            (REVIEW_SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
    return source_version, target_version
