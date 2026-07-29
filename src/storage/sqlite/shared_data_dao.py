# 提供本机跨账号公共覆盖数据的独立 SQLite 访问层。
"""本机公共覆盖数据库 DAO。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any


SCHEMA_VERSION = 2
SCHEMA_DIR = Path(__file__).resolve().parent / "schema" / "shared"
MIGRATIONS = {
    1: SCHEMA_DIR / "001_shared_data.sql",
    2: SCHEMA_DIR / "002_shared_data_migration_log.sql",
}


class SharedDataError(RuntimeError):
    """公共覆盖数据库缺失、损坏或事务失败。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SharedDataDao:
    """管理一个不随账号切换的本机公共覆盖数据库。"""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._connection: sqlite3.Connection | None = sqlite3.connect(
                self.database_path,
                timeout=10.0,
            )
        except sqlite3.Error as exc:
            raise SharedDataError(
                f"无法打开公共覆盖数据库：{self.database_path}"
            ) from exc
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 10000")
        try:
            self._initialize_or_migrate()
            self._validate_schema()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> "SharedDataDao":
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            connection.close()
            self._connection = None

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise SharedDataError("公共覆盖数据库 DAO 已关闭")
        return self._connection

    def _table_exists(self, table: str) -> bool:
        row = self._db().execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return row is not None

    def _initialize_or_migrate(self) -> None:
        connection = self._db()
        if not self._table_exists("schema_migration"):
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """CREATE TABLE schema_migration (
                           version INTEGER PRIMARY KEY,
                           applied_at_utc TEXT NOT NULL
                       )"""
                )
                connection.executescript(
                    MIGRATIONS[1].read_text(encoding="utf-8")
                )
                connection.execute(
                    "INSERT INTO database_profile VALUES (1, 'app_shared', ?, ?)",
                    (_utc_now(), _utc_now()),
                )
                connection.execute(
                    "INSERT INTO schema_migration(version, applied_at_utc) VALUES (1, ?)",
                    (_utc_now(),),
                )
                connection.commit()
            except (OSError, sqlite3.Error) as exc:
                connection.rollback()
                raise SharedDataError("无法初始化公共覆盖数据库") from exc

        row = connection.execute(
            "SELECT MAX(version) AS version FROM schema_migration"
        ).fetchone()
        version = int(row["version"] or 0)
        if version > SCHEMA_VERSION:
            raise SharedDataError(
                f"公共覆盖数据库版本 {version} 高于程序支持版本 {SCHEMA_VERSION}"
            )
        for target_version in range(version + 1, SCHEMA_VERSION + 1):
            migration = MIGRATIONS.get(target_version)
            if migration is None:
                raise SharedDataError(f"缺少公共覆盖数据库迁移 v{target_version}")
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.executescript(migration.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migration(version, applied_at_utc) VALUES (?, ?)",
                    (target_version, _utc_now()),
                )
                connection.commit()
            except (OSError, sqlite3.Error) as exc:
                connection.rollback()
                raise SharedDataError(
                    f"公共覆盖数据库迁移 v{target_version} 失败"
                ) from exc

    def _validate_schema(self) -> None:
        connection = self._db()
        row = connection.execute(
            "SELECT MAX(version) AS version FROM schema_migration"
        ).fetchone()
        if row is None or int(row["version"] or 0) != SCHEMA_VERSION:
            raise SharedDataError("公共覆盖数据库结构版本不完整")
        profile = connection.execute(
            "SELECT database_kind FROM database_profile WHERE singleton_id = 1"
        ).fetchone()
        if profile is None or profile["database_kind"] != "app_shared":
            raise SharedDataError("文件不是 NTE 本机公共覆盖数据库")

    def get_shape_bonus_override(
        self,
        logical_character_key: str,
    ) -> dict[str, Any] | None:
        key = str(logical_character_key or "").strip()
        row = self._db().execute(
            """SELECT logical_character_key, representative_character_id,
                      shape_label, shape_grid_count, based_on_dataset_id,
                      updated_at_utc
               FROM logical_character_shape_bonus_override
               WHERE logical_character_key = ?""",
            (key,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["properties"] = [
            dict(property_row)
            for property_row in self._db().execute(
                """SELECT property_id, display_value, ordinal
                   FROM logical_character_shape_bonus_property_override
                   WHERE logical_character_key = ?
                   ORDER BY ordinal""",
                (key,),
            )
        ]
        result["source_kind"] = "shared_override"
        return result

    def upsert_shape_bonus_override(
        self,
        logical_character_key: str,
        *,
        representative_character_id: int,
        shape_label: str,
        shape_grid_count: int,
        properties: Sequence[Mapping[str, Any]],
        based_on_dataset_id: str | None = None,
    ) -> dict[str, Any]:
        key = str(logical_character_key or "").strip()
        if not key:
            raise ValueError("logical_character_key 不能为空")
        if int(representative_character_id) <= 0:
            raise ValueError("representative_character_id 必须为正整数")
        if not str(shape_label or "").strip() or int(shape_grid_count) <= 0:
            raise ValueError("额外形状标签和格数无效")

        normalized: list[tuple[str, float, int]] = []
        seen_properties: set[str] = set()
        for ordinal, raw in enumerate(properties):
            property_id = str(raw.get("property_id") or "").strip()
            display_value = float(raw.get("display_value", 0.0))
            if not property_id or display_value < 0:
                raise ValueError("额外形状覆盖包含无效属性或数值")
            if property_id in seen_properties:
                raise ValueError(f"额外形状覆盖包含重复属性：{property_id}")
            seen_properties.add(property_id)
            normalized.append((property_id, display_value, ordinal))

        connection = self._db()
        now = _utc_now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO logical_character_shape_bonus_override(
                       logical_character_key, representative_character_id,
                       shape_label, shape_grid_count, based_on_dataset_id,
                       updated_at_utc
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(logical_character_key) DO UPDATE SET
                       representative_character_id =
                           excluded.representative_character_id,
                       shape_label = excluded.shape_label,
                       shape_grid_count = excluded.shape_grid_count,
                       based_on_dataset_id = excluded.based_on_dataset_id,
                       updated_at_utc = excluded.updated_at_utc""",
                (
                    key,
                    int(representative_character_id),
                    str(shape_label).strip(),
                    int(shape_grid_count),
                    str(based_on_dataset_id) if based_on_dataset_id else None,
                    now,
                ),
            )
            connection.execute(
                """DELETE FROM logical_character_shape_bonus_property_override
                   WHERE logical_character_key = ?""",
                (key,),
            )
            connection.executemany(
                """INSERT INTO logical_character_shape_bonus_property_override(
                       logical_character_key, property_id, display_value, ordinal
                   ) VALUES (?, ?, ?, ?)""",
                [
                    (key, property_id, display_value, ordinal)
                    for property_id, display_value, ordinal in normalized
                ],
            )
            connection.execute(
                "UPDATE database_profile SET updated_at_utc = ? WHERE singleton_id = 1",
                (now,),
            )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise SharedDataError("无法保存公共额外形状覆盖") from exc
        result = self.get_shape_bonus_override(key)
        assert result is not None
        return result

    def delete_shape_bonus_override(self, logical_character_key: str) -> bool:
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """DELETE FROM logical_character_shape_bonus_override
                   WHERE logical_character_key = ?""",
                (str(logical_character_key or "").strip(),),
            )
            if cursor.rowcount:
                connection.execute(
                    """UPDATE database_profile SET updated_at_utc = ?
                       WHERE singleton_id = 1""",
                    (_utc_now(),),
                )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise SharedDataError("无法删除公共额外形状覆盖") from exc
        return bool(cursor.rowcount)

    def migration_completed(self, migration_key: str) -> bool:
        row = self._db().execute(
            "SELECT 1 FROM data_migration WHERE migration_key = ?",
            (str(migration_key),),
        ).fetchone()
        return row is not None

    def record_migration(
        self,
        migration_key: str,
        *,
        source_fingerprint: str | None,
        details: Mapping[str, Any],
    ) -> None:
        try:
            with self._db():
                self._db().execute(
                    """INSERT INTO data_migration(
                           migration_key, source_fingerprint,
                           completed_at_utc, details_json
                       ) VALUES (?, ?, ?, ?)""",
                    (
                        str(migration_key),
                        source_fingerprint,
                        _utc_now(),
                        json.dumps(dict(details), ensure_ascii=False, sort_keys=True),
                    ),
                )
        except sqlite3.Error as exc:
            raise SharedDataError("无法记录公共覆盖数据迁移") from exc

    def apply_shape_bonus_migration(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        migration_key: str,
        source_fingerprint: str | None,
        details: Mapping[str, Any],
    ) -> int:
        """在一个事务中导入旧版差异并写入完成标记。

        已存在的用户覆盖优先，不会被迁移数据覆盖。
        """

        normalized_records: list[
            tuple[str, int, str, int, str | None, list[tuple[str, float, int]]]
        ] = []
        for raw in records:
            key = str(raw.get("logical_character_key") or "").strip()
            representative_id = int(raw.get("representative_character_id") or 0)
            shape_label = str(raw.get("shape_label") or "").strip()
            grid_count = int(raw.get("shape_grid_count") or 0)
            if not key or representative_id <= 0 or not shape_label or grid_count <= 0:
                raise ValueError(f"旧版额外形状记录无效：{key or '<empty>'}")
            properties: list[tuple[str, float, int]] = []
            seen: set[str] = set()
            for ordinal, property_row in enumerate(raw.get("properties") or ()):
                property_id = str(property_row.get("property_id") or "").strip()
                value = float(property_row.get("display_value", 0.0))
                if not property_id or value < 0 or property_id in seen:
                    raise ValueError(f"旧版额外形状属性无效：{key}")
                seen.add(property_id)
                properties.append((property_id, value, ordinal))
            normalized_records.append(
                (
                    key,
                    representative_id,
                    shape_label,
                    grid_count,
                    (
                        str(raw.get("based_on_dataset_id"))
                        if raw.get("based_on_dataset_id")
                        else None
                    ),
                    properties,
                )
            )

        connection = self._db()
        now = _utc_now()
        inserted = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM data_migration WHERE migration_key = ?",
                (str(migration_key),),
            ).fetchone():
                connection.rollback()
                return 0
            for (
                key,
                representative_id,
                shape_label,
                grid_count,
                dataset_id,
                properties,
            ) in normalized_records:
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO logical_character_shape_bonus_override(
                           logical_character_key, representative_character_id,
                           shape_label, shape_grid_count, based_on_dataset_id,
                           updated_at_utc
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        key,
                        representative_id,
                        shape_label,
                        grid_count,
                        dataset_id,
                        now,
                    ),
                )
                if not cursor.rowcount:
                    continue
                inserted += 1
                connection.executemany(
                    """INSERT INTO logical_character_shape_bonus_property_override(
                           logical_character_key, property_id,
                           display_value, ordinal
                       ) VALUES (?, ?, ?, ?)""",
                    [
                        (key, property_id, value, ordinal)
                        for property_id, value, ordinal in properties
                    ],
                )
            migration_details = dict(details)
            migration_details["inserted_override_count"] = inserted
            connection.execute(
                """INSERT INTO data_migration(
                       migration_key, source_fingerprint,
                       completed_at_utc, details_json
                   ) VALUES (?, ?, ?, ?)""",
                (
                    str(migration_key),
                    source_fingerprint,
                    now,
                    json.dumps(
                        migration_details,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )
            connection.execute(
                "UPDATE database_profile SET updated_at_utc = ? WHERE singleton_id = 1",
                (now,),
            )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise SharedDataError("旧版公共覆盖差异迁移失败") from exc
        return inserted
