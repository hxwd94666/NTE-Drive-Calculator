# 提供游戏资料库公式域所需的窄只读证据快照。
"""Read-only static evidence used by the formula and model-support catalog."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from src.storage.sqlite.static_game_data_dao import (
    StaticGameDataError,
    resolve_static_database,
)
from src.storage.sqlite.static_game_data_metadata import SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class StaticFormulaEvidenceSnapshot:
    """Counts of formal, normalized records; no raw payload is exposed."""

    dataset_id: str
    importer_version: int
    schema_version: int
    skill_damage_rows: int
    skill_damage_modifier_rows: int
    awakening_effect_rows: int
    awakening_skill_level_bonus_rows: int
    fork_rows: int
    fork_modifier_rows: int
    buff_definition_rows: int
    gameplay_effect_definition_rows: int
    buff_modifier_rows: int
    formal_dot_tag_rows: int
    formal_dot_tag_assets: int
    formal_attachment_tag_assets: int
    final_damage_up_modifier_rows: int
    dot_scoped_final_damage_up_modifier_rows: int


@dataclass(frozen=True, slots=True)
class StaticReactionCurve:
    """One official 16-tier reaction curve, kept separate from player labels."""

    source_effect_id: str
    values: tuple[float, ...]


class StaticCatalogFormulaQueries:
    """Own the fixed SQL used by the formula catalog.

    The UI cannot supply SQL, table names, fields, tags, or property names.  The
    connection is always opened with SQLite ``mode=ro`` and schema-checked.
    """

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = resolve_static_database(database_path)
        try:
            self._connection: sqlite3.Connection | None = sqlite3.connect(
                f"{self.database_path.as_uri()}?mode=ro",
                uri=True,
            )
        except sqlite3.Error as exc:
            raise StaticGameDataError(
                "无法只读打开公式资料所需的静态数据库"
            ) from exc
        self._connection.row_factory = sqlite3.Row
        try:
            version = self._scalar(
                "SELECT MAX(version) FROM schema_migration"
            )
        except sqlite3.Error as exc:
            self.close()
            raise StaticGameDataError("静态数据库缺少公式资料 schema") from exc
        if int(version or 0) != SCHEMA_VERSION:
            self.close()
            raise StaticGameDataError(
                f"不支持的静态数据库结构版本：{version!r}；需要 {SCHEMA_VERSION}"
            )

    def __enter__(self) -> "StaticCatalogFormulaQueries":
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        connection = self._connection
        if connection is not None:
            connection.close()
            self._connection = None

    def _scalar(self, sql: str, parameters: tuple[object, ...] = ()) -> object:
        if self._connection is None:
            raise StaticGameDataError("公式资料静态查询已关闭")
        row = self._connection.execute(sql, parameters).fetchone()
        if row is None:
            raise StaticGameDataError("公式资料静态查询没有返回记录")
        return row[0]

    def _table_count(self, table: str) -> int:
        queries = {
            "skill_damage": "SELECT COUNT(*) FROM skill_damage",
            "skill_damage_modifier": "SELECT COUNT(*) FROM skill_damage_modifier",
            "character_awaken_effect": "SELECT COUNT(*) FROM character_awaken_effect",
            "character_awaken_skill_level_bonus": (
                "SELECT COUNT(*) FROM character_awaken_skill_level_bonus"
            ),
            "fork_item": "SELECT COUNT(*) FROM fork_item",
            "fork_modify_value": "SELECT COUNT(*) FROM fork_modify_value",
            "buff_modifier": "SELECT COUNT(*) FROM buff_modifier",
        }
        try:
            sql = queries[table]
        except KeyError as exc:
            raise StaticGameDataError("公式资料请求了未登记的静态表") from exc
        return int(self._scalar(sql))

    def evidence_snapshot(self) -> StaticFormulaEvidenceSnapshot:
        """Return only auditable aggregate evidence from known schema-v29 tables."""

        if self._connection is None:
            raise StaticGameDataError("公式资料静态查询已关闭")
        dataset = self._connection.execute(
            "SELECT dataset_id, importer_version FROM dataset"
        ).fetchone()
        if dataset is None:
            raise StaticGameDataError("静态数据库缺少 dataset 元信息")

        dot_rows = int(self._scalar(
            """
            SELECT COUNT(*) FROM combat_blueprint_tag
            WHERE lower(tag_name) = lower(?)
            """,
            ("State.Damage.Dot",),
        ))
        dot_assets = int(self._scalar(
            """
            SELECT COUNT(DISTINCT source_asset_path) FROM combat_blueprint_tag
            WHERE lower(tag_name) = lower(?)
            """,
            ("State.Damage.Dot",),
        ))
        attachment_assets = int(self._scalar(
            """
            SELECT COUNT(DISTINCT source_asset_path) FROM combat_blueprint_tag
            WHERE lower(tag_name) = lower(?)
            """,
            ("State.Damage.Attachment",),
        ))
        final_damage_rows = int(self._scalar(
            """
            SELECT COUNT(*) FROM buff_modifier
            WHERE lower(property_id) = lower(?)
            """,
            ("FinalDamageUp",),
        ))
        dot_final_rows = int(self._scalar(
            """
            SELECT COUNT(*) FROM buff_modifier
            WHERE lower(property_id) = lower(?)
              AND (
                  lower(source_require_tags_json) LIKE ?
                  OR lower(target_require_tags_json) LIKE ?
              )
            """,
            ("FinalDamageUp", "%state.damage.dot%", "%state.damage.dot%"),
        ))
        definition_counts = {
            str(row[0]): int(row[1])
            for row in self._connection.execute(
                """
                SELECT definition_kind, COUNT(*)
                FROM buff_definition GROUP BY definition_kind
                """
            )
        }
        return StaticFormulaEvidenceSnapshot(
            dataset_id=str(dataset["dataset_id"]),
            importer_version=int(dataset["importer_version"]),
            schema_version=SCHEMA_VERSION,
            skill_damage_rows=self._table_count("skill_damage"),
            skill_damage_modifier_rows=self._table_count("skill_damage_modifier"),
            awakening_effect_rows=self._table_count("character_awaken_effect"),
            awakening_skill_level_bonus_rows=self._table_count(
                "character_awaken_skill_level_bonus"
            ),
            fork_rows=self._table_count("fork_item"),
            fork_modifier_rows=self._table_count("fork_modify_value"),
            buff_definition_rows=definition_counts.get("buff", 0),
            gameplay_effect_definition_rows=definition_counts.get(
                "gameplay_effect", 0
            ),
            buff_modifier_rows=self._table_count("buff_modifier"),
            formal_dot_tag_rows=dot_rows,
            formal_dot_tag_assets=dot_assets,
            formal_attachment_tag_assets=attachment_assets,
            final_damage_up_modifier_rows=final_damage_rows,
            dot_scoped_final_damage_up_modifier_rows=dot_final_rows,
        )

    def reaction_damage_curves(self) -> tuple[StaticReactionCurve, ...]:
        """Return registered reaction values in source-tier order.

        Player-facing names are deliberately owned by the presentation service;
        a resource suffix must never be used to infer a character owner.
        """

        if self._connection is None:
            raise StaticGameDataError("公式资料静态查询已关闭")
        rows = self._connection.execute(
            """
            SELECT curve.source_effect_id, point.source_tier, point.value
            FROM combat_level_curve AS curve
            JOIN combat_level_curve_point AS point USING (curve_id)
            WHERE curve.damage_kind = 'reaction'
              AND curve.source_effect_id IN (?, ?, ?, ?, ?)
            ORDER BY curve.source_effect_id, point.source_tier
            """,
            (
                "GE_ActorReaction_1_Damage",
                "GE_ActorReaction_1_1019_Damage",
                "Buff_Reaction_5_new",
                "Buff_Reaction_5_new_1036",
                "Buff_Reaction_4_new",
            ),
        ).fetchall()
        grouped: dict[str, list[tuple[int, float]]] = {}
        for row in rows:
            grouped.setdefault(str(row["source_effect_id"]), []).append((
                int(row["source_tier"]),
                float(row["value"]),
            ))
        curves = []
        for effect_id, points in grouped.items():
            if tuple(tier for tier, _value in points) != tuple(range(16)):
                raise StaticGameDataError(
                    f"环合曲线 {effect_id!r} 缺少完整源档 0–15"
                )
            curves.append(StaticReactionCurve(
                source_effect_id=effect_id,
                values=tuple(value for _tier, value in points),
            ))
        return tuple(curves)

    def topple_level_curve(self) -> tuple[float, ...]:
        """Return the official per-character-level topple base values."""

        if self._connection is None:
            raise StaticGameDataError("公式资料静态查询已关闭")
        rows = self._connection.execute(
            """
            SELECT character_level, value
            FROM combat_level_curve_point
            WHERE curve_id = ?
            ORDER BY ordinal
            """,
            ("topple:character_level",),
        ).fetchall()
        levels = tuple(int(float(row["character_level"])) for row in rows)
        if levels != tuple(range(1, 81)):
            raise StaticGameDataError("倾陷等级曲线缺少完整角色等级 1–80")
        return tuple(float(row["value"]) for row in rows)


__all__ = [
    "StaticCatalogFormulaQueries",
    "StaticFormulaEvidenceSnapshot",
    "StaticReactionCurve",
]
