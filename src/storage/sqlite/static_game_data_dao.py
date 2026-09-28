# 提供标准化静态游戏数据库的只读访问层。
"""标准化静态游戏数据库的只读访问层。"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from types import TracebackType
from typing import Any, Iterable, NoReturn

import tempfile
import threading
from functools import lru_cache
from .static_game_data_metadata import (
    MINIMUM_SUPPORTED_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SUMMARY_TABLES,
)

STATIC_DATABASE_ENV = "NTE_GAME_STATIC_DB"
_DEFAULT_LOGICAL_CHARACTER_IDS = {"protagonist": 1051}
_ROLE_TEMPLATE_CLASSIFICATIONS = {
    "available_character",
    "scheduled_character",
    "playable",
}

_SHARED_STATIC_CONNECTIONS: dict[str, sqlite3.Connection] = {}
_SHARED_STATIC_LOCK = threading.Lock()
# SQLite 以 serialized 模式编译（``sqlite3.threadsafety == 3``），并发 execute
# 本身是安全的；这把锁隔离的是「执行中」与「关闭连接」，避免查询线程拿到已关闭
# 的连接，并让进程退出时的回收与正在进行的读互不干扰。
_SHARED_STATIC_EXECUTE_LOCK = threading.RLock()


@lru_cache(maxsize=4)
def _resolved_temp_dir() -> Path:
    """Cache the temp root; it does not change within a process."""

    return Path(tempfile.gettempdir()).resolve()


@lru_cache(maxsize=256)
def _is_temp_path(path: Path) -> bool:
    """Check whether an already-resolved path resides in temp directories.

    ``StaticGameDataDao`` 传入的路径已由 ``resolve_static_database`` 解析过；
    这里不再二次解析。Windows 上每次 ``Path.resolve()`` 都要逐个路径段调用
    ``GetFinalPathName``，是短生命周期开连的主要开销。
    """
    try:
        parts = {part.lower() for part in path.parts}
        if parts & {"tmp", "temp", ".tmp", "pytest"}:
            return True
        return _resolved_temp_dir() in path.parents
    except Exception:
        return False


def _configure_static_connection(connection: sqlite3.Connection) -> None:
    """Apply standard PRAGMAs for high-performance read-only static access."""
    connection.row_factory = sqlite3.Row
    for pragma in (
        "PRAGMA journal_mode=WAL",
        "PRAGMA cache_size=-32000",
        "PRAGMA mmap_size=268435456",
        "PRAGMA temp_store=MEMORY",
    ):
        try:
            connection.execute(pragma)
        except sqlite3.Error:
            pass


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


from src.storage.sqlite.static_game_data_combat_blueprint_queries import (
    StaticGameDataCombatBlueprintQueriesMixin,
)
from src.storage.sqlite.static_game_data_buff_queries import (
    StaticGameDataBuffQueriesMixin,
)
from src.storage.sqlite.static_game_data_extended_queries import StaticGameDataExtendedQueriesMixin
from src.storage.sqlite.static_game_data_encounter_queries import (
    StaticGameDataEncounterQueriesMixin,
)
from src.storage.sqlite.static_game_data_terminology_queries import (
    StaticGameDataTerminologyQueriesMixin,
)
from src.storage.sqlite.static_game_data_progression_queries import (
    StaticGameDataProgressionQueriesMixin,
)
from src.storage.sqlite.static_game_data_character_growth_queries import (
    StaticGameDataCharacterGrowthQueriesMixin,
)
from src.storage.sqlite.static_game_data_skill_damage_queries import (
    StaticGameDataSkillDamageQueriesMixin,
)
from src.storage.sqlite.static_game_data_weight_queries import (
    StaticGameDataWeightQueriesMixin,
)


class StaticGameDataDao(
    StaticGameDataProgressionQueriesMixin,
    StaticGameDataCharacterGrowthQueriesMixin,
    StaticGameDataTerminologyQueriesMixin,
    StaticGameDataEncounterQueriesMixin,
    StaticGameDataBuffQueriesMixin,
    StaticGameDataCombatBlueprintQueriesMixin,
    StaticGameDataSkillDamageQueriesMixin,
    StaticGameDataWeightQueriesMixin,
    StaticGameDataExtendedQueriesMixin,
):
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
        resolved_version = int(version or 0)
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
        with _SHARED_STATIC_EXECUTE_LOCK:
            with _SHARED_STATIC_LOCK:
                _SHARED_STATIC_CONNECTIONS.pop(norm_key, None)
            try:
                connection.close()
            except sqlite3.Error:
                pass
        self._connection = None

    @classmethod
    def close_shared_connections(cls, database_path: str | Path | None = None) -> None:
        """Close shared static database connections across the process."""
        with _SHARED_STATIC_EXECUTE_LOCK:
            with _SHARED_STATIC_LOCK:
                if database_path is not None:
                    norm_key = os.path.normcase(
                        str(Path(database_path).expanduser().resolve())
                    )
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
        with _SHARED_STATIC_EXECUTE_LOCK:
            connection = self._connection
            if connection is None:
                raise StaticGameDataError("静态数据库 DAO 已关闭")
            try:
                return [
                    dict(row)
                    for row in connection.execute(sql, tuple(parameters))
                ]
            except sqlite3.ProgrammingError as exc:
                # 共享连接可能已被其他线程 close(force=True) 或进程回收关闭；
                # 转换成领域错误，避免工作线程拿到裸露的 sqlite3 异常。
                if "closed" not in str(exc).lower():
                    raise
                raise StaticGameDataError("静态数据库 DAO 已关闭") from exc

    def _one(self, sql: str, parameters: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self._rows(sql, parameters)
        return rows[0] if rows else None

    @staticmethod
    def _raise_static_data_error(message: str) -> NoReturn:
        raise StaticGameDataError(message)

    def summary(self) -> dict[str, Any]:
        dataset = self._one(
            "SELECT dataset_id, importer_version, built_at_utc FROM dataset"
        )
        if dataset is None:
            raise StaticGameDataError("静态数据库缺少数据集元信息")
        counts = {}
        available_tables: set[str] | None = None
        if self._schema_version != SCHEMA_VERSION:
            available_tables = {
                str(row["name"])
                for row in self._rows(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        for table in SUMMARY_TABLES:
            if available_tables is not None and table not in available_tables:
                continue
            row = self._one(f"SELECT COUNT(*) AS count FROM {table}")
            counts[table] = int((row or {}).get("count", 0))
        return {
            "schema_version": self._schema_version,
            "database_path": str(self.database_path),
            "dataset": dataset,
            "counts": counts,
        }

    def dataset_info(self) -> dict[str, Any]:
        """只返回数据集元信息，供仅需 dataset 的调用方免去 summary 的逐表计数。"""

        dataset = self._one(
            "SELECT dataset_id, importer_version, built_at_utc FROM dataset"
        )
        if dataset is None:
            raise StaticGameDataError("静态数据库缺少数据集元信息")
        return dataset

    def application_setting_defaults(self) -> dict[str, dict[str, Any]]:
        defaults: dict[str, dict[str, Any]] = {}
        for row in self._rows(
            "SELECT setting_key, value_json FROM application_setting_default ORDER BY setting_key"
        ):
            try:
                value = json.loads(row["value_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise StaticGameDataError(
                    f"静态设置默认值 {row['setting_key']!r} 不是有效 JSON"
                ) from exc
            if not isinstance(value, dict):
                raise StaticGameDataError(
                    f"静态设置默认值 {row['setting_key']!r} 不是 JSON 对象"
                )
            defaults[str(row["setting_key"])] = value
        return defaults

    def get_character_shape_bonus(
        self, character_id: int,
    ) -> dict[str, Any] | None:
        row = self._one(
            """
            SELECT a.character_id, a.logical_character_key,
                   b.representative_character_id, b.shape_label,
                   b.shape_grid_count, b.source_kind
            FROM character_annotation AS a
            JOIN logical_character_shape_bonus AS b
              ON b.logical_character_key = a.logical_character_key
            WHERE a.character_id = ?
            """,
            (int(character_id),),
        )
        if row is None:
            return None
        row["properties"] = self._rows(
            """
            SELECT p.property_id, p.display_value, p.ordinal,
                   a.display_name_zh, a.filter_name_zh, a.show_percent
            FROM logical_character_shape_bonus_property AS p
            JOIN equipment_attribute AS a
              ON a.attribute_id = p.property_id
            WHERE p.logical_character_key = ?
            ORDER BY p.ordinal
            """,
            (str(row["logical_character_key"]),),
        )
        return row

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

    def get_logical_character_key(self, character_id: int) -> str | None:
        """Resolve an actual character or transformation ID to its shared rule key."""

        row = self._one(
            """
            SELECT logical_character_key
            FROM character_annotation
            WHERE character_id = ?
            """,
            (int(character_id),),
        )
        return str(row["logical_character_key"]) if row is not None else None

    def list_role_template_characters(
        self,
        preferred_character_ids: Iterable[int] = (),
    ) -> list[dict[str, Any]]:
        """Return one actual character row for every logical role.

        Account-observed avatar IDs win for variant-only roles.  With no
        account evidence the protagonist uses the female official ID 1051.
        Combat transformations resolve through their logical key but never
        create an additional role-menu entry.
        """

        characters = self.list_characters()
        by_id = {
            int(character["character_id"]): character
            for character in characters
        }
        selected: dict[str, dict[str, Any]] = {}
        for character in characters:
            if character.get("classification") not in _ROLE_TEMPLATE_CLASSIFICATIONS:
                continue
            logical_key = str(
                character.get("logical_character_key")
                or f"character:{character['character_id']}"
            )
            selected.setdefault(logical_key, character)

        preferred_logical_keys: set[str] = set()
        for raw_character_id in preferred_character_ids:
            try:
                character_id = int(raw_character_id)
            except (TypeError, ValueError):
                continue
            preferred_character = by_id.get(character_id)
            if preferred_character is None:
                continue
            preferred_logical_key = self.get_logical_character_key(
                character_id
            )
            if (
                not preferred_logical_key
                or preferred_logical_key in preferred_logical_keys
            ):
                continue
            if (
                preferred_character.get("classification")
                == "available_avatar_variant"
            ):
                selected[preferred_logical_key] = preferred_character
                preferred_logical_keys.add(preferred_logical_key)

        for logical_key, default_character_id in _DEFAULT_LOGICAL_CHARACTER_IDS.items():
            if logical_key in selected:
                continue
            default_character = by_id.get(default_character_id)
            if (
                default_character is not None
                and self.get_logical_character_key(default_character_id) == logical_key
            ):
                selected[logical_key] = default_character

        return sorted(
            selected.values(),
            key=lambda character: int(character["character_id"]),
        )

    def get_character_graduation_template(
        self, character_id: int,
    ) -> dict[str, Any] | None:
        """读取构建期生成的固定毕业模板；运行时不再搜索词条组合。"""

        template = self._one(
            """
            SELECT character_id, source_kind, fork_id, fork_level,
                   fork_refinement_level, core_suit_id,
                   core_main_property_id, drive_area, extra_shape_count,
                   benchmark_damage, profile_json, equipment_json,
                   generated_at_utc
            FROM character_graduation_template
            WHERE character_id = ?
            """,
            (int(character_id),),
        )
        if template is None:
            return None
        template["profile"] = json.loads(template.pop("profile_json"))
        template["equipment"] = json.loads(template.pop("equipment_json"))
        return template

    def list_character_graduation_templates(self) -> list[dict[str, Any]]:
        return [
            template
            for row in self._rows(
                "SELECT character_id FROM character_graduation_template ORDER BY character_id"
            )
            if (
                template := self.get_character_graduation_template(
                    int(row["character_id"])
                )
            ) is not None
        ]

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

    def list_equipment_attributes(self) -> list[dict[str, Any]]:
        """返回可用于装备词条、核心筛选和官方蓝图的属性 ID。"""

        rows = self._rows(
            """
            SELECT attribute_id, display_name_zh, filter_name_zh,
                   random_attribute_name_zh, attribute_type, show_percent,
                   show_outside, show_inside, score, icon_path, source_row_id
            FROM equipment_attribute
            ORDER BY attribute_id
            """
        )
        for row in rows:
            for field in ("show_percent", "show_outside", "show_inside"):
                row[field] = bool(row[field])
        return rows

    def get_equipment_attribute(self, attribute_id: str) -> dict[str, Any] | None:
        """按官方属性 ID 查询装备属性定义。"""

        raw_attribute_id = str(attribute_id).strip()
        if not raw_attribute_id:
            raise ValueError("attribute_id 不能为空")
        return next(
            (
                attribute
                for attribute in self.list_equipment_attributes()
                if attribute["attribute_id"] == raw_attribute_id
            ),
            None,
        )

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

    def get_equipment_item(self, item_id: str) -> dict[str, Any] | None:
        """按游戏官方物品 ID 返回一条装备模板。"""

        raw_item_id = str(item_id).strip()
        if not raw_item_id:
            raise ValueError("item_id 不能为空")
        return next(
            (
                item
                for item in self.list_equipment_items()
                if item["item_id"] == raw_item_id
            ),
            None,
        )

    def evaluate_equipment_base_attribute_curve(
        self,
        curve_id: str,
        level: float,
    ) -> float | None:
        """按官方插值模式读取装备主属性在指定等级的数值。"""

        return self.evaluate_equipment_base_attribute_curve_levels(
            curve_id, (level,),
        )[0]

    def evaluate_equipment_base_attribute_curve_levels(
        self,
        curve_id: str,
        levels: Iterable[float],
    ) -> list[float | None]:
        """一次读取曲线后求值多个等级，避免逐级重复查询同一条曲线。"""

        requested = [float(level) for level in levels]
        curve = self._one(
            """
            SELECT interpolation_mode, default_value
            FROM equipment_base_attribute_curve
            WHERE curve_id = ?
            """,
            (str(curve_id),),
        )
        if curve is None:
            return [None] * len(requested)
        points = self._rows(
            """
            SELECT level, value
            FROM equipment_base_attribute_point
            WHERE curve_id = ?
            ORDER BY level
            """,
            (str(curve_id),),
        )
        if not points:
            default_value = curve.get("default_value")
            fallback = None if default_value is None else float(default_value)
            return [fallback] * len(requested)

        mode = str(curve.get("interpolation_mode") or "")
        return [
            self._interpolate_equipment_curve(mode, points, level)
            for level in requested
        ]

    @staticmethod
    def _interpolate_equipment_curve(
        interpolation_mode: str,
        points: list[dict[str, Any]],
        level: float,
    ) -> float:
        """在一个已读取的曲线上按官方插值模式求值。"""

        target = float(level)
        if target <= float(points[0]["level"]):
            return float(points[0]["value"])
        if target >= float(points[-1]["level"]):
            return float(points[-1]["value"])

        previous = points[0]
        for current in points[1:]:
            current_level = float(current["level"])
            if target > current_level:
                previous = current
                continue
            if interpolation_mode == "RCIM_Constant":
                return float(previous["value"])
            previous_level = float(previous["level"])
            span = current_level - previous_level
            if span <= 0:
                return float(current["value"])
            ratio = (target - previous_level) / span
            return float(previous["value"]) + (
                float(current["value"]) - float(previous["value"])
            ) * ratio
        return float(points[-1]["value"])
