# 在静态构建owner的原事务中借用连接，仅查询并物化，不结束连接或事务。
from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any, Iterable, NoReturn

from src.storage.sqlite.catalog.static_game_data_queries import StaticGameDataError, StaticGameDataQueriesMixin


class StaticGameDataBuildDao(StaticGameDataQueriesMixin):
    """构建专用borrowed reader；没有close、BEGIN或shared registry权限。"""

    def __init__(self, connection: sqlite3.Connection, *, database_path: Path) -> None:
        self._connection = connection
        self.database_path = Path(database_path).expanduser().resolve()
        row = self._one("SELECT MAX(version) AS version FROM schema_migration")
        try:
            self._schema_version = int((row or {}).get("version") or 0)
        except (TypeError, ValueError) as error:
            raise StaticGameDataError("静态构建数据库结构版本元信息无效") from error
        if self._schema_version <= 0:
            raise StaticGameDataError("静态构建数据库缺少结构版本")

    @property
    def schema_version(self) -> int:
        return self._schema_version

    def _rows(self, sql: str, parameters: Iterable[Any] = ()) -> list[dict[str, Any]]:
        cursor = self._connection.cursor()
        try:
            # row_factory只属于本次cursor，不改变owner连接的返回类型。
            cursor.row_factory = sqlite3.Row
            cursor.execute(sql, tuple(parameters))
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def _one(self, sql: str, parameters: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self._rows(sql, parameters)
        return rows[0] if rows else None

    @staticmethod
    def _raise_static_data_error(message: str) -> NoReturn:
        raise StaticGameDataError(message)
