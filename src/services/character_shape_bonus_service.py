# 管理跨账号共享、且不随安装包覆盖的额外形状配置。
"""Resolve and persist public extra-shape overrides outside bundled static data."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from src.app import runtime
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao, StaticGameDataError


_OVERRIDE_DATABASE_NAME = "public_overrides.sqlite3"


def public_shape_bonus_override_path(
    database_path: str | Path | None = None,
) -> Path:
    """Return the writable, installation-local shared override database path.

    ``database_path`` is intentionally available to tests and maintenance tools.
    Normal GUI use stores the file under ``DATA_ROOT`` so installer updates never
    overwrite it with the bundled immutable ``game_static.sqlite3``.
    """

    if database_path is not None:
        return Path(database_path).expanduser().resolve()
    data_root = getattr(runtime, "DATA_ROOT", None)
    if data_root is None:
        raise StaticGameDataError("运行时数据目录尚未初始化，无法读取公共额外形状配置")
    return Path(data_root).resolve() / _OVERRIDE_DATABASE_NAME


def _resolve_override_path(
    override_database_path: str | Path | None,
    *,
    static_database_path: str | Path | None = None,
) -> Path | None:
    if override_database_path is not None:
        return public_shape_bonus_override_path(override_database_path)
    if getattr(runtime, "DATA_ROOT", None) is not None:
        return public_shape_bonus_override_path()
    # Isolated maintenance/tests may provide a static fixture without booting
    # the GUI runtime.  Keep their shared override beside that fixture.
    if static_database_path is not None:
        return Path(static_database_path).expanduser().resolve().parent / _OVERRIDE_DATABASE_NAME
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _shape_grid_count(shape_label: str) -> int:
    match = re.search(r"(\d+)", str(shape_label or ""))
    grid_count = int(match.group(1)) if match is not None else 0
    if grid_count <= 0:
        raise ValueError(
            f"额外形状标签必须包含有效格数，例如 Type-3（当前值：{shape_label!r}）"
        )
    return grid_count


def _open_override_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE IF NOT EXISTS character_shape_bonus_override(
               logical_character_key TEXT PRIMARY KEY,
               representative_character_id INTEGER NOT NULL,
               shape_label TEXT NOT NULL,
               shape_grid_count INTEGER NOT NULL,
               updated_at_utc TEXT NOT NULL
           )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS character_shape_bonus_override_property(
               logical_character_key TEXT NOT NULL,
               property_id TEXT NOT NULL,
               display_value REAL NOT NULL,
               ordinal INTEGER NOT NULL,
               PRIMARY KEY(logical_character_key, property_id),
               FOREIGN KEY(logical_character_key)
                   REFERENCES character_shape_bonus_override(logical_character_key)
                   ON DELETE CASCADE
           )"""
    )
    return connection


def _override_record(
    logical_character_key: str,
    *,
    override_database_path: str | Path | None = None,
    static_database_path: str | Path | None = None,
) -> dict[str, Any] | None:
    path = _resolve_override_path(
        override_database_path, static_database_path=static_database_path,
    )
    if path is None:
        return None
    if not path.is_file():
        return None
    try:
        connection = _open_override_database(path)
    except sqlite3.Error as exc:
        raise StaticGameDataError(f"无法打开公共额外形状配置：{path}") from exc
    try:
        row = connection.execute(
            """SELECT representative_character_id, shape_label, shape_grid_count,
                      updated_at_utc
               FROM character_shape_bonus_override
               WHERE logical_character_key = ?""",
            (logical_character_key,),
        ).fetchone()
        if row is None:
            return None
        properties = [
            dict(property_row)
            for property_row in connection.execute(
                """SELECT property_id, display_value, ordinal
                   FROM character_shape_bonus_override_property
                   WHERE logical_character_key = ? ORDER BY ordinal""",
                (logical_character_key,),
            )
        ]
        return {**dict(row), "properties": properties}
    except sqlite3.Error as exc:
        raise StaticGameDataError("无法读取公共额外形状配置") from exc
    finally:
        connection.close()


def get_effective_character_shape_bonus(
    static_dao: StaticGameDataDao,
    character_id: int,
    *,
    override_database_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Return the bundled rule overlaid by a durable public user override."""

    raw_character_id = int(character_id)
    character = static_dao.get_character(raw_character_id)
    if character is None:
        return None
    logical_key = str(character.get("logical_character_key") or "")
    bundled = static_dao.get_character_shape_bonus(raw_character_id)
    if not logical_key:
        return bundled
    override = _override_record(
        logical_key,
        override_database_path=override_database_path,
        static_database_path=static_dao.database_path,
    )
    if override is None:
        return bundled
    return {
        "character_id": raw_character_id,
        "logical_character_key": logical_key,
        "representative_character_id": int(override["representative_character_id"]),
        "shape_label": str(override["shape_label"]),
        "shape_grid_count": int(override["shape_grid_count"]),
        "source_kind": "public_override",
        "updated_at_utc": str(override["updated_at_utc"]),
        "properties": list(override["properties"]),
    }


def save_public_character_shape_bonus(
    character_id: int,
    *,
    shape_label: str,
    property_values: Mapping[str, float],
    database_path: str | Path | None = None,
    override_database_path: str | Path | None = None,
) -> dict[str, Any]:
    """Save one logical role's shared override without modifying static data.

    ``database_path`` still selects the bundled static database for validation,
    preserving the previous maintenance-tool API.  Mutable data is written only
    to ``public_overrides.sqlite3`` under the writable data root.
    """

    raw_character_id = int(character_id)
    if raw_character_id <= 0:
        raise ValueError("character_id 必须为正整数")
    if not isinstance(property_values, Mapping):
        raise ValueError("额外形状加成必须是属性映射")
    normalized_properties: list[tuple[str, float]] = []
    for property_id, raw_value in property_values.items():
        normalized_property_id = str(property_id or "").strip()
        value = float(raw_value)
        if not normalized_property_id or value < 0:
            raise ValueError("额外形状加成包含无效属性或数值")
        normalized_properties.append((normalized_property_id, value))

    with StaticGameDataDao(database_path) as static_dao:
        static_database_path = static_dao.database_path
        character = static_dao.get_character(raw_character_id)
        if character is None or not character.get("logical_character_key"):
            raise ValueError(f"公共静态数据库没有角色 {raw_character_id} 的逻辑角色标识")
        logical_key = str(character["logical_character_key"])
        bundled = static_dao.get_character_shape_bonus(raw_character_id) or {}
        known_properties = {
            str(row["attribute_id"])
            for row in static_dao.list_equipment_attributes()
        }
        unknown = sorted(
            property_id for property_id, _value in normalized_properties
            if property_id not in known_properties
        )
        if unknown:
            raise ValueError(f"额外形状加成包含未知官方属性：{'、'.join(unknown)}")

    existing = _override_record(
        logical_key,
        override_database_path=override_database_path,
        static_database_path=static_database_path,
    )
    effective_label = str(shape_label or "").strip() or str(
        (existing or bundled).get("shape_label") or ""
    ).strip()
    grid_count = _shape_grid_count(effective_label)
    path = _resolve_override_path(
        override_database_path, static_database_path=static_database_path,
    )
    assert path is not None
    try:
        connection = _open_override_database(path)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT INTO character_shape_bonus_override(
                   logical_character_key, representative_character_id,
                   shape_label, shape_grid_count, updated_at_utc
               ) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(logical_character_key) DO UPDATE SET
                   representative_character_id = excluded.representative_character_id,
                   shape_label = excluded.shape_label,
                   shape_grid_count = excluded.shape_grid_count,
                   updated_at_utc = excluded.updated_at_utc""",
            (logical_key, raw_character_id, effective_label, grid_count, _utc_now()),
        )
        connection.execute(
            "DELETE FROM character_shape_bonus_override_property WHERE logical_character_key = ?",
            (logical_key,),
        )
        connection.executemany(
            """INSERT INTO character_shape_bonus_override_property(
                   logical_character_key, property_id, display_value, ordinal
               ) VALUES (?, ?, ?, ?)""",
            [
                (logical_key, property_id, value, ordinal)
                for ordinal, (property_id, value) in enumerate(normalized_properties)
            ],
        )
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        raise StaticGameDataError("无法保存公共额外形状配置") from exc
    finally:
        connection.close()

    with StaticGameDataDao(database_path) as static_dao:
        result = get_effective_character_shape_bonus(
            static_dao, raw_character_id,
            override_database_path=override_database_path,
        )
    assert result is not None
    return result
