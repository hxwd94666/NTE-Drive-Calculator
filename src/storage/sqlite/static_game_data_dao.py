# 提供标准化静态游戏数据库的只读访问层。
"""标准化静态游戏数据库的只读访问层。"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 2
STATIC_DATABASE_ENV = "NTE_GAME_STATIC_DB"

SUMMARY_TABLES = (
    "source_file",
    "source_row",
    "character",
    "character_annotation",
    "equipment_attribute",
    "equipment_shape",
    "equipment_suit",
    "equipment_suit_effect",
    "equipment_item",
    "equipment_plan",
    "fork_type",
    "fork_item",
)


class StaticGameDataError(RuntimeError):
    """静态数据库缺失或版本不兼容。"""


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


class StaticGameDataDao:
    """面向 schema v2 静态数据库的轻量查询边界。

    连接始终使用 SQLite 只读模式，避免界面或计算代码意外修改开发者生成的数据包。
    """

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = resolve_static_database(database_path)
        uri = f"{self.database_path.as_uri()}?mode=ro"
        try:
            self._connection = sqlite3.connect(uri, uri=True)
        except sqlite3.Error as exc:
            raise StaticGameDataError(
                f"无法打开静态数据库：{self.database_path}"
            ) from exc
        self._connection.row_factory = sqlite3.Row
        try:
            version_row = self._connection.execute(
                "SELECT MAX(version) AS version FROM schema_migration"
            ).fetchone()
        except sqlite3.Error as exc:
            self.close()
            raise StaticGameDataError("文件不是 NTE 静态游戏数据库") from exc
        version = version_row["version"] if version_row is not None else None
        if version != SCHEMA_VERSION:
            self.close()
            raise StaticGameDataError(
                f"不支持的静态数据库结构版本：{version!r}；需要 {SCHEMA_VERSION}"
            )

    def __enter__(self) -> "StaticGameDataDao":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def close(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            connection.close()
            self._connection = None

    def _rows(self, sql: str, parameters: Iterable[Any] = ()) -> list[dict[str, Any]]:
        if self._connection is None:
            raise StaticGameDataError("静态数据库 DAO 已关闭")
        return [dict(row) for row in self._connection.execute(sql, tuple(parameters))]

    def _one(self, sql: str, parameters: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self._rows(sql, parameters)
        return rows[0] if rows else None

    def summary(self) -> dict[str, Any]:
        dataset = self._one(
            "SELECT dataset_id, game_version, importer_version, built_at_utc FROM dataset"
        )
        if dataset is None:
            raise StaticGameDataError("静态数据库缺少数据集元信息")
        counts = {
            table: self._one(f"SELECT COUNT(*) AS count FROM {table}")["count"]
            for table in SUMMARY_TABLES
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "database_path": str(self.database_path),
            "dataset": dataset,
            "counts": counts,
        }

    def list_characters(self) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT c.character_id, c.name_zh, c.name_text_table, c.name_text_key,
                   c.element_type, c.group_type, c.actor_path, c.mainland_show_time,
                   c.source_row_id, a.logical_character_key,
                   a.canonical_character_id, a.classification, a.annotation_source
            FROM character AS c
            LEFT JOIN character_annotation AS a USING (character_id)
            ORDER BY c.character_id
            """
        )

    def get_character(self, character_id: int) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT c.character_id, c.name_zh, c.name_text_table, c.name_text_key,
                   c.element_type, c.group_type, c.actor_path, c.mainland_show_time,
                   c.source_row_id, a.logical_character_key,
                   a.canonical_character_id, a.classification, a.annotation_source
            FROM character AS c
            LEFT JOIN character_annotation AS a USING (character_id)
            WHERE c.character_id = ?
            """,
            (character_id,),
        )

    def list_shapes(self) -> list[dict[str, Any]]:
        shapes = self._rows(
            """
            SELECT shape_id, cell_count, first_grid_delta_x, first_grid_delta_y,
                   source_row_id
            FROM equipment_shape
            ORDER BY shape_id
            """
        )
        cells = self._rows(
            """
            SELECT shape_id, ordinal, x, y
            FROM equipment_shape_cell
            ORDER BY shape_id, ordinal
            """
        )
        cells_by_shape: dict[str, list[dict[str, Any]]] = {}
        for cell in cells:
            cells_by_shape.setdefault(cell.pop("shape_id"), []).append(cell)
        for shape in shapes:
            shape["cells"] = cells_by_shape.get(shape["shape_id"], [])
        return shapes

    def list_suits(self) -> list[dict[str, Any]]:
        suits = self._rows(
            """
            SELECT suit_id, name_zh, name_text_table, name_text_key, icon_path,
                   source_row_id
            FROM equipment_suit
            ORDER BY suit_id
            """
        )
        required_shapes = self._rows(
            """
            SELECT suit_id, ordinal, shape_id
            FROM equipment_suit_required_shape
            ORDER BY suit_id, ordinal
            """
        )
        effects = self._rows(
            """
            SELECT suit_id, required_count, modify_pack_id, buff_object_path,
                   description_zh, description_text_table, description_text_key,
                   reapply_after_revive, source_row_id
            FROM equipment_suit_effect
            ORDER BY suit_id, required_count
            """
        )
        shapes_by_suit: dict[str, list[str]] = {}
        for row in required_shapes:
            shapes_by_suit.setdefault(row["suit_id"], []).append(row["shape_id"])
        effects_by_suit: dict[str, list[dict[str, Any]]] = {}
        for effect in effects:
            effect["reapply_after_revive"] = bool(effect["reapply_after_revive"])
            effects_by_suit.setdefault(effect.pop("suit_id"), []).append(effect)
        for suit in suits:
            suit["required_shape_ids"] = shapes_by_suit.get(suit["suit_id"], [])
            suit["effects"] = effects_by_suit.get(suit["suit_id"], [])
        return suits

    def get_suit(self, suit_id: str) -> dict[str, Any] | None:
        return next((suit for suit in self.list_suits() if suit["suit_id"] == suit_id), None)

    def list_equipment_items(self, kind: str | None = None) -> list[dict[str, Any]]:
        if kind not in (None, "module", "core"):
            raise ValueError("equipment kind must be 'module', 'core', or None")
        where = "" if kind is None else "WHERE kind = ?"
        parameters = () if kind is None else (kind,)
        rows = self._rows(
            f"""
            SELECT item_id, kind, quality, name_zh, name_text_table, name_text_key,
                   geometry_id, geometry_enum, grid_count, suit_id, suit_type_enum,
                   max_level, random_base_attribute_pool_id,
                   random_base_attribute_count, random_sub_attribute_pool_id,
                   random_sub_attribute_count, random_sub_attribute_max_count,
                   strength_pack_id, icon_path, plan_icon_path, is_guide_item,
                   source_row_id
            FROM equipment_item
            {where}
            ORDER BY item_id
            """,
            parameters,
        )
        for row in rows:
            row["is_guide_item"] = bool(row["is_guide_item"])
        return rows

    def list_forks(self) -> list[dict[str, Any]]:
        rows = self._rows(
            """
            SELECT f.fork_id, f.name_zh, f.name_text_table, f.name_text_key,
                   f.description_zh, f.quality, f.fork_type_id,
                   t.name_zh AS fork_type_name_zh, f.raw_group_type,
                   f.upgrade_pack_id, f.breakthrough_pack_id, f.star_pack_id,
                   f.max_breakthrough, f.max_star, f.icon_path, f.card_path,
                   f.painting_path, f.exclusive_character_ids_json,
                   f.source_row_id
            FROM fork_item AS f
            LEFT JOIN fork_type AS t USING (fork_type_id)
            ORDER BY f.fork_id
            """
        )
        for row in rows:
            row["exclusive_character_ids"] = json.loads(
                row.pop("exclusive_character_ids_json")
            )
        return rows

    def get_equipment_plan(self, character_id: int) -> dict[str, Any] | None:
        plan = self._one(
            """
            SELECT p.character_id, c.name_zh AS character_name_zh,
                   p.core_item_id, core.name_zh AS core_name_zh,
                   p.core_level, p.module_level, p.reference_score,
                   p.background_path, p.character_image_path, p.source_row_id
            FROM equipment_plan AS p
            JOIN character AS c USING (character_id)
            JOIN equipment_item AS core ON core.item_id = p.core_item_id
            WHERE p.character_id = ?
            """,
            (character_id,),
        )
        if plan is None:
            return None
        plan["core_attribute_ids"] = [
            row["attribute_id"]
            for row in self._rows(
                """
                SELECT attribute_id FROM equipment_plan_core_attribute
                WHERE character_id = ? ORDER BY ordinal
                """,
                (character_id,),
            )
        ]
        plan["recommended_attribute_ids"] = [
            row["attribute_id"]
            for row in self._rows(
                """
                SELECT attribute_id FROM equipment_plan_recommended_attribute
                WHERE character_id = ? ORDER BY ordinal
                """,
                (character_id,),
            )
        ]
        plan["cells"] = self._rows(
            """
            SELECT row, column, anchor_item_id FROM equipment_plan_cell
            WHERE character_id = ? ORDER BY row, column
            """,
            (character_id,),
        )
        plan["module_item_ids"] = [
            row["item_id"]
            for row in self._rows(
                """
                SELECT item_id FROM equipment_plan_module
                WHERE character_id = ? ORDER BY ordinal
                """,
                (character_id,),
            )
        ]
        return plan

    def get_source_payload(self, relative_path: str, row_key: str) -> Any | None:
        row = self._one(
            """
            SELECT r.payload_json
            FROM source_row AS r
            JOIN source_file AS f USING (source_file_id)
            WHERE f.relative_path = ? AND r.row_key = ?
            """,
            (relative_path, str(row_key)),
        )
        if row is None or row["payload_json"] is None:
            return None
        return json.loads(row["payload_json"])
