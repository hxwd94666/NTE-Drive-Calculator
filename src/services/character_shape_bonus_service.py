# 管理跨账号共享、随静态库更新覆盖的额外形状配置。
"""Read and update public extra-shape rules in the static game database."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from src.storage.sqlite.static_game_data_dao import StaticGameDataDao, StaticGameDataError


def _shape_grid_count(shape_label: str) -> int:
    match = re.search(r"(\d+)", str(shape_label or ""))
    grid_count = int(match.group(1)) if match is not None else 0
    if grid_count <= 0:
        raise ValueError(
            f"额外形状标签必须包含有效格数，例如 Type-3（当前值：{shape_label!r}）"
        )
    return grid_count


def get_effective_character_shape_bonus(
    static_dao: StaticGameDataDao,
    character_id: int,
) -> dict[str, Any] | None:
    """Return the single public rule shared by every account."""

    return static_dao.get_character_shape_bonus(int(character_id))


def save_public_character_shape_bonus(
    character_id: int,
    *,
    shape_label: str,
    property_values: Mapping[str, float],
    database_path: str | Path | None = None,
) -> dict[str, Any]:
    """Save one logical role's public rule directly to the static database.

    The rule is intentionally shared by every account. A later application
    update replaces ``game_static.sqlite3`` with the bundled public defaults.
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

    effective_label = str(shape_label or "").strip() or str(
        bundled.get("shape_label") or ""
    ).strip()
    grid_count = _shape_grid_count(effective_label)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(static_database_path)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT INTO logical_character_shape_bonus(
                   logical_character_key, representative_character_id,
                   shape_label, shape_grid_count, source_kind
               ) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(logical_character_key) DO UPDATE SET
                   representative_character_id = excluded.representative_character_id,
                   shape_label = excluded.shape_label,
                   shape_grid_count = excluded.shape_grid_count""",
            (logical_key, raw_character_id, effective_label, grid_count, "official_role_profile"),
        )
        connection.execute(
            "DELETE FROM logical_character_shape_bonus_property WHERE logical_character_key = ?",
            (logical_key,),
        )
        connection.executemany(
            """INSERT INTO logical_character_shape_bonus_property(
                   logical_character_key, property_id, display_value, ordinal
               ) VALUES (?, ?, ?, ?)""",
            [
                (logical_key, property_id, value, ordinal)
                for ordinal, (property_id, value) in enumerate(normalized_properties)
            ],
        )
        connection.commit()
    except sqlite3.Error as exc:
        if connection is not None:
            connection.rollback()
        raise StaticGameDataError("无法保存公共额外形状配置到静态数据库") from exc
    finally:
        if connection is not None:
            connection.close()

    with StaticGameDataDao(database_path) as static_dao:
        result = get_effective_character_shape_bonus(static_dao, raw_character_id)
    assert result is not None
    return result
