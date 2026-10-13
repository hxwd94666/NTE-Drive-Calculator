# 提供标准化静态游戏数据库的只读访问层。
"""标准化静态游戏数据库的只读访问层。"""

from __future__ import annotations

from contextlib import contextmanager
import os
import sqlite3
import sys
from pathlib import Path
from types import TracebackType
from typing import Any, Iterable, Iterator, NoReturn

import tempfile
import threading
from src.storage.sqlite.static_game_data_metadata import (
    MINIMUM_SUPPORTED_SCHEMA_VERSION,
    SCHEMA_VERSION,
)

STATIC_DATABASE_ENV = "NTE_GAME_STATIC_DB"
_SHARED_STATIC_CONNECTIONS: dict[str, sqlite3.Connection] = {}
_SHARED_STATIC_LOCK = threading.Lock()


def _is_temp_path(path: Path) -> bool:
    """Check if the given path resides within temporary directories."""
    try:
        resolved = path.resolve()
        temp_dir = Path(tempfile.gettempdir()).resolve()
        if temp_dir in resolved.parents:
            return True
        parts = {p.lower() for p in resolved.parts}
        if parts & {"tmp", "temp", ".tmp", "pytest"}:
            return True
        return False
    except Exception:
        return False


def _configure_static_connection(connection: sqlite3.Connection) -> None:
    """Apply standard PRAGMAs for high-performance read-only static access."""
    connection.row_factory = sqlite3.Row
    for pragma in (
        "PRAGMA cache_size=-32000",
        "PRAGMA mmap_size=268435456",
        "PRAGMA temp_store=MEMORY",
    ):
        try:
            connection.execute(pragma)
        except sqlite3.Error:
            pass


def static_database_candidates() -> list[Path]:
    """按优先级返回开发环境和打包环境中的静态数据库候选路径。"""

    candidates: list[Path] = []
    configured = os.environ.get(STATIC_DATABASE_ENV)
    if configured:
        candidates.append(Path(configured).expanduser())

    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidates.append(Path(frozen_root) / "data" / "game_static.sqlite3")

    executable_dir = Path(sys.executable).resolve().parent
    project_root = Path(__file__).resolve().parents[3]
    candidates.extend(
        (
            executable_dir / "data" / "game_static.sqlite3",
            project_root / "data" / "game_static.sqlite3",
            project_root / "build_resources" / "game_static.sqlite3",
        )
    )

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def resolve_static_database(database_path: str | Path | None = None) -> Path:
    """解析显式路径、环境变量或随程序提供的静态数据库。"""

    if database_path is not None:
        resolved = Path(database_path).expanduser().resolve()
        if resolved.is_file():
            return resolved
        raise StaticGameDataError(f"静态数据库不存在：{resolved}")
    for candidate in static_database_candidates():
        if candidate.is_file():
            return candidate
    checked = "、".join(str(path) for path in static_database_candidates())
    raise StaticGameDataError(f"找不到静态数据库；已检查：{checked}")


from src.storage.sqlite.catalog.static_game_data_queries import StaticGameDataError, StaticGameDataQueriesMixin


class StaticGameDataDao(StaticGameDataQueriesMixin):
    """面向当前发行静态数据库 schema 的轻量查询边界。

    连接始终使用 SQLite 只读模式，并在多次查询间复用连接以消除系统调用开销。
    """

    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        expected_schema_version: int | None = None,
        reuse_connection: bool | None = None,
    ) -> None:
        expected_version = (
            int(expected_schema_version)
            if expected_schema_version is not None
            else None
        )
        if expected_version is not None and expected_version <= 0:
            raise ValueError("expected_schema_version 必须为正整数")
        self._schema_version = SCHEMA_VERSION
        self.database_path = resolve_static_database(database_path)
        norm_key = os.path.normcase(str(self.database_path))

        if reuse_connection is None:
            self._reuse_connection = not _is_temp_path(self.database_path)
        else:
            self._reuse_connection = bool(reuse_connection)

        self._connection: sqlite3.Connection | None = None
        if self._reuse_connection:
            with _SHARED_STATIC_LOCK:
                existing = _SHARED_STATIC_CONNECTIONS.get(norm_key)
                if existing is not None:
                    try:
                        existing.execute("SELECT 1")
                        self._connection = existing
                    except sqlite3.Error:
                        _SHARED_STATIC_CONNECTIONS.pop(norm_key, None)
                        existing = None
                if self._connection is None:
                    uri = f"{self.database_path.as_uri()}?mode=ro"
                    try:
                        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
                    except sqlite3.Error as exc:
                        raise StaticGameDataError(
                            f"无法打开静态数据库：{self.database_path}"
                        ) from exc
                    _configure_static_connection(conn)
                    _SHARED_STATIC_CONNECTIONS[norm_key] = conn
                    self._connection = conn
        else:
            uri = f"{self.database_path.as_uri()}?mode=ro"
            try:
                self._connection = sqlite3.connect(uri, uri=True)
            except sqlite3.Error as exc:
                raise StaticGameDataError(
                    f"无法打开静态数据库：{self.database_path}"
                ) from exc
            _configure_static_connection(self._connection)

        try:
            version_row = self._connection.execute(
                "SELECT MAX(version) AS version FROM schema_migration"
            ).fetchone()
        except sqlite3.Error as exc:
            self.close(force=True)
            raise StaticGameDataError("文件不是 NTE 静态游戏数据库") from exc
        version = version_row["version"] if version_row is not None else None
        try:
            resolved_version = int(version or 0)
        except (TypeError, ValueError) as exc:
            # 构造失败也必须物理关掉自身；异常traceback存活不应延长连接寿命。
            self.close(force=True)
            raise StaticGameDataError("静态数据库结构版本元信息无效") from exc
        if expected_version is not None and resolved_version != expected_version:
            self.close(force=True)
            raise StaticGameDataError(
                f"不支持的静态数据库结构版本：{version!r}；需要 {expected_version}"
            )
        if expected_version is None and resolved_version not in range(
            MINIMUM_SUPPORTED_SCHEMA_VERSION, SCHEMA_VERSION + 1,
        ):
            self.close(force=True)
            raise StaticGameDataError(
                f"不支持的静态数据库结构版本：{version!r}；支持 "
                f"{MINIMUM_SUPPORTED_SCHEMA_VERSION} 至 {SCHEMA_VERSION}"
            )
        self._schema_version = resolved_version

    @property
    def schema_version(self) -> int:
        # 只公开连接已经校验的结构版本，不为取得它枚举业务表。
        return self._schema_version

    def __enter__(self) -> "StaticGameDataDao":
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self, *, force: bool = False) -> None:
        connection = getattr(self, "_connection", None)
        if connection is None:
            return
        if self._reuse_connection and not force:
            self._connection = None
            return
        norm_key = os.path.normcase(str(self.database_path))
        with _SHARED_STATIC_LOCK:
            # 专属B只关闭自身，不摘掉同路径共享A的注册。
            if _SHARED_STATIC_CONNECTIONS.get(norm_key) is connection:
                _SHARED_STATIC_CONNECTIONS.pop(norm_key, None)
        try:
            connection.close()
        except sqlite3.Error:
            pass
        self._connection = None

    @classmethod
    def close_shared_connections(cls, database_path: str | Path | None = None) -> None:
        """Close shared static database connections across the process."""
        with _SHARED_STATIC_LOCK:
            if database_path is not None:
                norm_key = os.path.normcase(str(Path(database_path).expanduser().resolve()))
                conn = _SHARED_STATIC_CONNECTIONS.pop(norm_key, None)
                if conn is not None:
                    try:
                        conn.close()
                    except sqlite3.Error:
                        pass
            else:
                for conn in _SHARED_STATIC_CONNECTIONS.values():
                    try:
                        conn.close()
                    except sqlite3.Error:
                        pass
                _SHARED_STATIC_CONNECTIONS.clear()

    def _rows(self, sql: str, parameters: Iterable[Any] = ()) -> list[dict[str, Any]]:
        if self._connection is None:
            raise StaticGameDataError("静态数据库 DAO 已关闭")
        return [dict(row) for row in self._connection.execute(sql, tuple(parameters))]

    def _one(self, sql: str, parameters: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self._rows(sql, parameters)
        return rows[0] if rows else None

    @staticmethod
    def _raise_static_data_error(message: str) -> NoReturn:
        raise StaticGameDataError(message)

    @contextmanager
    def read_consistent_state(self) -> Iterator["StaticGameDataDao"]:
        connection = self._connection
        if connection is None:
            raise StaticGameDataError("静态数据库 DAO 已关闭")
        owns = not connection.in_transaction
        if owns:
            connection.execute("BEGIN")
        try:
            yield self
        finally:
            if owns:
                connection.rollback()
