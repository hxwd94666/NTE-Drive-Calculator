# 提供用户数据 SQLite 的连接、迁移与基础工具。
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .user_data_support import (
    ALLOCATION_STRATEGIES,
    BASE_SCHEMA_VERSION,
    DEFAULT_SCHEMA_PATH,
    DEFAULT_SNAPSHOT_RETENTION_COUNT,
    SNAPSHOT_SOURCES,
    SUIT_REQUIREMENT_MODES,
    SYNC_METHODS,
    USER_MIGRATIONS,
    SCHEMA_VERSION,
    UserDataError,
    UserDataValidationError,
    _decoded,
    _integer,
    _json,
    _mark_duplicate_modules,
    _plain_object,
    _utc_now,
)

class UserDataDaoCore:
    """单个应用账号所拥有数据的读写边界。

    数据库只保存原始游戏 ID，不映射到旧 JSON 名称，也不重复保存静态显示数据。
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        account_id: str | None = None,
        account_name: str | None = None,
        schema_path: str | Path = DEFAULT_SCHEMA_PATH,
    ) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        existed = self.database_path.is_file()
        if not existed and not account_id:
            raise UserDataError("创建用户数据库时必须提供 account_id")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._connection: sqlite3.Connection | None = sqlite3.connect(
                self.database_path, timeout=10.0
            )
        except sqlite3.Error as exc:
            raise UserDataError(f"无法打开用户数据库：{self.database_path}") from exc
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 10000")
        try:
            if not existed:
                self._initialize(
                    Path(schema_path),
                    account_id=str(account_id),
                    account_name=str(account_name or account_id),
                )
            self._migrate_schema()
            self._validate_schema()
        except BaseException:
            self.close()
            if not existed:
                self.database_path.unlink(missing_ok=True)
            raise

    def __enter__(self) -> "UserDataDao":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def close(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            connection.close()
            self._connection = None

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise UserDataError("用户数据库 DAO 已关闭")
        return self._connection

    def _initialize(self, schema_path: Path, *, account_id: str, account_name: str) -> None:
        if not schema_path.is_file():
            raise UserDataError(f"用户数据库结构文件不存在：{schema_path}")
        connection = self._db()
        try:
            connection.executescript(schema_path.read_text(encoding="utf-8"))
            now = _utc_now()
            connection.execute(
                "INSERT INTO schema_migration(version, applied_at_utc) VALUES (?, ?)",
                (BASE_SCHEMA_VERSION, now),
            )
            connection.execute(
                """
                INSERT INTO database_profile(
                    singleton_id, account_id, account_name, created_at_utc, updated_at_utc
                ) VALUES (1, ?, ?, ?, ?)
                """,
                (account_id, account_name, now, now),
            )
            connection.execute(
                """
                INSERT INTO sync_settings(
                    singleton_id, inventory_sync_method, equipment_apply_method,
                    capture_device_id, raw_capture_enabled,
                    inventory_settle_seconds, updated_at_utc
                ) VALUES (1, 'nte_core', 'nte_core', NULL, 0, 5.0, ?)
                """,
                (now,),
            )
            connection.commit()
            connection.execute("PRAGMA journal_mode = WAL")
        except (OSError, sqlite3.Error) as exc:
            raise UserDataError("无法初始化用户数据库") from exc

    def _migrate_schema(self) -> None:
        connection = self._db()
        try:
            row = connection.execute(
                "SELECT MAX(version) AS version FROM schema_migration"
            ).fetchone()
        except sqlite3.Error as exc:
            raise UserDataError("文件不是 NTE 用户数据库") from exc
        version = int(row["version"] or 0) if row is not None else 0
        if version > SCHEMA_VERSION:
            raise UserDataError(
                f"用户数据库结构版本 {version} 高于当前程序支持的 {SCHEMA_VERSION}"
            )
        try:
            for target_version in range(version + 1, SCHEMA_VERSION + 1):
                migration_path = USER_MIGRATIONS.get(target_version)
                if migration_path is None or not migration_path.is_file():
                    raise UserDataError(f"缺少用户数据库迁移脚本：v{target_version}")
                migration_sql = migration_path.read_text(encoding="utf-8")
                connection.execute("BEGIN IMMEDIATE")
                self._execute_migration_script(connection, migration_sql)
                connection.execute(
                    "INSERT INTO schema_migration(version, applied_at_utc) VALUES (?, ?)",
                    (target_version, _utc_now()),
                )
                connection.commit()
        except (OSError, sqlite3.Error) as exc:
            connection.rollback()
            raise UserDataError("无法升级用户数据库结构") from exc

    @staticmethod
    def _execute_migration_script(connection: sqlite3.Connection, script: str) -> None:
        """在调用方已经开启的事务中逐条执行迁移 SQL，避免 executescript 隐式提交。"""

        statement = ""
        for line in script.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                if statement.strip():
                    connection.execute(statement)
                statement = ""
        if statement.strip():
            raise UserDataError("用户数据库迁移脚本包含未结束的 SQL 语句")

    def _validate_schema(self) -> None:
        try:
            row = self._db().execute(
                "SELECT MAX(version) AS version FROM schema_migration"
            ).fetchone()
        except sqlite3.Error as exc:
            raise UserDataError("文件不是 NTE 用户数据库") from exc
        version = row["version"] if row is not None else None
        if version != SCHEMA_VERSION:
            raise UserDataError(
                f"不支持的用户数据库结构版本：{version!r}；需要 {SCHEMA_VERSION}"
            )

    def _rows(self, sql: str, parameters: Iterable[Any] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self._db().execute(sql, tuple(parameters))]

    def _one(self, sql: str, parameters: Iterable[Any] = ()) -> dict[str, Any] | None:
        row = self._db().execute(sql, tuple(parameters)).fetchone()
        return dict(row) if row is not None else None
