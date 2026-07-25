"""Write the shared extra-shape rule stored in the public static database."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.storage.sqlite.static_game_data_dao import (
    SCHEMA_VERSION,
    StaticGameDataError,
    resolve_static_database,
)


def _shape_grid_count(shape_label: str) -> int:
    match = re.search(r"(\d+)", str(shape_label or ""))
    grid_count = int(match.group(1)) if match is not None else 0
    if grid_count <= 0:
        raise ValueError(
            f"额外形状标签必须包含有效格数，例如 Type-3（当前值：{shape_label!r}）"
        )
    return grid_count


def save_public_character_shape_bonus(
    character_id: int,
    *,
    shape_label: str,
    property_values: Mapping[str, float],
    database_path: str | Path | None = None,
) -> dict[str, Any]:
    """Update one logical role's shared extra-shape rule.

    Shape bonuses are game-rule configuration rather than account-specific
    scoring preferences.  The logical-key table makes protagonist/variant
    character IDs share the same value across every account on this install.
    """

    raw_character_id = int(character_id)
    if raw_character_id <= 0:
        raise ValueError("character_id 必须为正整数")
    normalized_label = str(shape_label or "").strip()
    if not isinstance(property_values, Mapping):
        raise ValueError("额外形状加成必须是属性映射")
    normalized_properties: list[tuple[str, float]] = []
    for property_id, raw_value in property_values.items():
        normalized_property_id = str(property_id or "").strip()
        value = float(raw_value)
        if not normalized_property_id or value < 0:
            raise ValueError("额外形状加成包含无效属性或数值")
        normalized_properties.append((normalized_property_id, value))

    resolved_path = resolve_static_database(database_path)
    try:
        connection = sqlite3.connect(resolved_path)
        connection.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        raise StaticGameDataError(f"无法打开公共静态数据库：{resolved_path}") from exc
    try:
        version_row = connection.execute(
            "SELECT MAX(version) AS version FROM schema_migration"
        ).fetchone()
        if version_row is None or int(version_row["version"] or 0) != SCHEMA_VERSION:
            raise StaticGameDataError("公共静态数据库版本不兼容，无法保存额外形状")
        character = connection.execute(
            """SELECT logical_character_key
               FROM character_annotation WHERE character_id = ?""",
            (raw_character_id,),
        ).fetchone()
        if character is None or not character["logical_character_key"]:
            raise ValueError(f"公共静态数据库没有角色 {raw_character_id} 的逻辑角色标识")
        logical_key = str(character["logical_character_key"])
        existing = connection.execute(
            """SELECT representative_character_id, source_kind, shape_label
               FROM logical_character_shape_bonus
               WHERE logical_character_key = ?""",
            (logical_key,),
        ).fetchone()
        # Editing only the numeric bonus must not fail merely because a stale
        # UI draft did not retain the already configured label.
        effective_label = normalized_label or str(
            existing["shape_label"] if existing is not None else ""
        ).strip()
        grid_count = _shape_grid_count(effective_label)
        known_properties = {
            str(row[0])
            for row in connection.execute("SELECT attribute_id FROM equipment_attribute")
        }
        unknown = sorted(
            property_id for property_id, _value in normalized_properties
            if property_id not in known_properties
        )
        if unknown:
            raise ValueError(f"额外形状加成包含未知官方属性：{'、'.join(unknown)}")

        connection.execute("BEGIN IMMEDIATE")
        representative_id = (
            int(existing["representative_character_id"])
            if existing is not None else raw_character_id
        )
        # Keep the schema's official/legacy provenance vocabulary intact: the
        # editable value is still local public game configuration, not an
        # account preference and not a workshop weight record.
        source_kind = str(existing["source_kind"]) if existing is not None else "official_role_profile"
        connection.execute(
            """INSERT INTO logical_character_shape_bonus(
                   logical_character_key, representative_character_id,
                   shape_label, shape_grid_count, source_kind
               ) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(logical_character_key) DO UPDATE SET
                   shape_label = excluded.shape_label,
                   shape_grid_count = excluded.shape_grid_count,
                   source_kind = excluded.source_kind""",
            (logical_key, representative_id, effective_label, grid_count, source_kind),
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
        connection.rollback()
        raise StaticGameDataError("无法保存公共角色额外形状") from exc
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

    # Graduation templates contain the total bonus from every matching shape
    # in the fixed blueprint.  Refresh them immediately so graduation score,
    # direct damage and replacement optimization cannot keep a stale
    # precomputed extra-shape contribution after a public edit.
    from tools.game_data.build_graduation_templates import populate_graduation_templates

    template_connection = sqlite3.connect(resolved_path)
    try:
        populate_graduation_templates(
            template_connection,
            database_path=resolved_path,
            config_dir=Path(__file__).resolve().parents[2] / "config",
        )
    except Exception as exc:
        raise StaticGameDataError("额外形状已写入，但刷新毕业基准失败") from exc
    finally:
        template_connection.close()

    # Re-open via the ordinary read-only API so callers receive exactly the
    # same normalized representation used by allocation and role pages.
    from src.storage.sqlite.static_game_data_dao import StaticGameDataDao

    with StaticGameDataDao(resolved_path) as dao:
        result = dao.get_character_shape_bonus(raw_character_id)
    assert result is not None
    return result
