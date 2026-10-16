# 只读核对账号身份并用 SQLite 一致性备份建立升级/迁入快照，不复制活动 WAL。
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3

from src.storage.sqlite.accounts.transfer_database import backup_account_database
from src.storage.sqlite.user_data_support import SCHEMA_VERSION
from src.storage.sqlite.shared_data_dao import SCHEMA_VERSION as SHARED_SCHEMA_VERSION


class DataSnapshotError(RuntimeError):
    """运行数据身份、版本或一致性副本校验失败。"""


def database_identity(path: Path, account_id: str | None = None) -> dict:
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
        version = int(db.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] or 0)
        limit = SCHEMA_VERSION if account_id is not None else SHARED_SCHEMA_VERSION
        if version > limit:
            raise DataSnapshotError(f"数据结构版本 {version} 高于当前程序支持的 {limit}；请使用匹配程序。")
        if account_id is not None:
            actual = db.execute("SELECT account_id FROM database_profile WHERE singleton_id=1").fetchone()
            if not actual or actual[0] != account_id:
                raise DataSnapshotError("账号索引与数据库身份冲突。")
        legacy_settings = False
        if account_id is not None:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            legacy_settings = ("application_setting_migration" not in tables
                               or db.execute("SELECT 1 FROM application_setting_migration LIMIT 1").fetchone() is None)
            if "application_setting_copy" in tables:
                row = db.execute("SELECT value_json FROM application_setting_copy WHERE setting_key='ui'").fetchone()
                legacy_settings |= bool(row and "theme" in json.loads(row[0]))
        return {"schema": version, "target_schema": limit, "account_id": account_id,
                "legacy_settings": legacy_settings}


def backup_database(source: Path, destination: Path) -> None:
    """只有真实备份才进行全库完整性检查，正常启动只查身份/schema。"""
    backup_account_database(source, destination)
    with closing(sqlite3.connect(destination)) as db:
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise DataSnapshotError("恢复点数据库校验失败，尚未迁移。")
