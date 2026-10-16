# 只读核对账号身份并用 SQLite 一致性备份建立升级/迁入快照，不复制活动 WAL。
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import re
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


def legacy_import_identity_needs_repair(path: Path, account_id: str, account_name: str) -> bool:
    """只识别历史 default 导入分叉；其他身份冲突与未来 schema 沿原门禁停止。"""
    try:
        database_identity(path, account_id)
        return False
    except DataSnapshotError:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
            db.execute("BEGIN")
            profile = db.execute("SELECT account_id,account_name FROM database_profile WHERE singleton_id=1").fetchone()
            version = db.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0]
            suffix = re.fullmatch(r"default_([0-9]+)", account_id)
            if (profile and profile == ("default", account_name) and account_name.strip()
                    and suffix and int(suffix[1]) >= 2 and 0 < version <= SCHEMA_VERSION):
                return True
        raise


def repair_legacy_import_identity(path: Path, account_id: str, account_name: str, *, rollback=False) -> None:
    """调用方持数据租约并已有一致性恢复点；单库事务只改 profile 身份，不改业务行或时间。"""
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=rw", uri=True, timeout=10)) as db:
        try:
            db.execute("BEGIN IMMEDIATE")
            before, after = (account_id, "default") if rollback else ("default", account_id)
            row = db.execute("SELECT account_id,account_name FROM database_profile WHERE singleton_id=1").fetchone()
            if row != (before, account_name):
                raise DataSnapshotError("修复期间账号身份已变化；保留恢复点，未覆盖当前数据。")
            cursor = db.execute("UPDATE database_profile SET account_id=? WHERE singleton_id=1 AND account_id=? AND account_name=?",
                                (after, before, account_name))
            if cursor.rowcount != 1:
                raise DataSnapshotError("账号标识修复未完成，原数据保留。")
            db.commit()
        except BaseException:
            db.rollback()
            raise
