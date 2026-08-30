# 静态游戏数据库的公共本地化术语查询。
"""Read-only localized terminology queries for the static game database."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable, NoReturn, Protocol, cast

from src.domain.static_catalog_terminology import (
    ForkCampaignRecord,
    LocalizedTermRecord,
    TermSourceKind,
)


class _TerminologyQuerySource(Protocol):
    def _one(
        self,
        sql: str,
        parameters: Iterable[Any] = (),
    ) -> dict[str, Any] | None: ...

    def _rows(
        self,
        sql: str,
        parameters: Iterable[Any] = (),
    ) -> list[dict[str, Any]]: ...

    def _raise_static_data_error(self, message: str) -> NoReturn: ...


class StaticGameDataTerminologyQueriesMixin(_TerminologyQuerySource):
    """Resolve formal names without falling back to unreadable raw identities."""

    def lookup_localized_term(
        self,
        entity_kind: str,
        stable_id: str,
        *,
        context: str | None,
    ) -> LocalizedTermRecord | None:
        """Resolve one exact stable identity without inventing a visible name.

        Context aliases are exact and case-sensitive. A missing alias falls
        through to exact canonical lookup, so ``Gold`` remains the formal
        capital item while only lowercase ``gold`` maps to ``Fons``.
        """

        kind = str(entity_kind or "").strip()
        requested_id = str(stable_id or "").strip()
        requested_context = str(context).strip() if context is not None else None
        if kind == "item":
            canonical_id = requested_id
            if requested_context:
                try:
                    alias = self._one(
                        """SELECT item_id FROM progression_item_alias
                           WHERE token = ? COLLATE BINARY
                             AND context = ? COLLATE BINARY""",
                        (requested_id, requested_context),
                    )
                except sqlite3.OperationalError:
                    return None
                if alias is not None:
                    canonical_id = str(alias["item_id"])
            central = self._lookup_central_localized_term(kind, canonical_id)
            if central is not None:
                return central
            try:
                row = self._one(
                    """SELECT item_id, names_json, name_text_table, name_text_key
                       FROM progression_item
                       WHERE item_id = ? COLLATE BINARY""",
                    (canonical_id,),
                )
            except sqlite3.OperationalError:
                return None
            if row is None:
                return None
            names = self._decode_names(
                row.get("names_json"),
                description=f"物品 {canonical_id!r}",
            )
            return self._record(
                entity_kind="item",
                canonical_id=str(row["item_id"]),
                names=names,
                text_table=row.get("name_text_table"),
                text_key=row.get("name_text_key"),
            )
        if kind in {"item_quality", "item_quality_color"}:
            central = self._lookup_central_localized_term(kind, requested_id)
            if central is not None:
                return central
            prefix = "grade" if kind == "item_quality" else "color"
            try:
                row = self._one(
                    f"""SELECT quality_id, {prefix}_names_json AS names_json,
                               {prefix}_text_table AS text_table,
                               {prefix}_text_key AS text_key
                        FROM item_quality_term
                        WHERE quality_id = ? COLLATE BINARY""",
                    (requested_id,),
                )
            except sqlite3.OperationalError:
                return None
            if row is None:
                return None
            names = self._decode_names(
                row.get("names_json"),
                description=f"物品品质 {requested_id!r}",
            )
            return self._record(
                entity_kind=kind,
                canonical_id=str(row["quality_id"]),
                names=names,
                text_table=row.get("text_table"),
                text_key=row.get("text_key"),
            )
        central = self._lookup_central_localized_term(kind, requested_id)
        if central is not None:
            return central
        return self._lookup_existing_localized_term(kind, requested_id)

    def list_fork_campaigns(self) -> tuple[ForkCampaignRecord, ...]:
        """Return formal campaigns newest first using official PoolIDMap order."""

        try:
            rows = self._rows(
                """SELECT pool_id, featured_fork_id, release_ordinal
                   FROM fork_lottery_campaign
                   ORDER BY release_ordinal DESC"""
            )
        except sqlite3.OperationalError:
            return ()
        records = []
        for row in rows:
            fork_id = str(row["featured_fork_id"])
            title = self._lookup_central_localized_term(
                "fork_campaign", str(row["pool_id"])
            )
            if title is None:
                self._raise_static_data_error(
                    f"弧盘限定卡池 {row['pool_id']!r} 缺少中央标题术语"
                )
            records.append(ForkCampaignRecord(
                pool_id=str(row["pool_id"]),
                featured_fork_id=fork_id,
                release_ordinal=int(row["release_ordinal"]),
                title=title,
            ))
        return tuple(records)

    def _lookup_central_localized_term(
        self,
        entity_kind: str,
        canonical_id: str,
    ) -> LocalizedTermRecord | None:
        try:
            identity = self._one(
                """SELECT source_kind, text_table, text_key
                   FROM localized_term
                   WHERE entity_kind = ? COLLATE BINARY
                     AND canonical_id = ? COLLATE BINARY""",
                (entity_kind, canonical_id),
            )
            if identity is None:
                return None
            name_rows = self._rows(
                """SELECT locale, display_name
                   FROM localized_term_name
                   WHERE entity_kind = ? COLLATE BINARY
                     AND canonical_id = ? COLLATE BINARY
                   ORDER BY locale""",
                (entity_kind, canonical_id),
            )
        except sqlite3.OperationalError:
            return None
        return LocalizedTermRecord(
            entity_kind=entity_kind,
            canonical_id=canonical_id,
            names={str(row["locale"]): str(row["display_name"]) for row in name_rows},
            text_table=(
                str(identity["text_table"])
                if identity.get("text_table") is not None else None
            ),
            text_key=(
                str(identity["text_key"])
                if identity.get("text_key") is not None else None
            ),
            source_kind=cast(TermSourceKind, str(identity["source_kind"])),
        )

    def _lookup_existing_localized_term(
        self,
        entity_kind: str,
        stable_id: str,
    ) -> LocalizedTermRecord | None:
        catalogs = {
            "character": (
                "character", "character_id", "name_zh",
                "name_text_table", "name_text_key",
            ),
            "fork": (
                "fork_item", "fork_id", "name_zh",
                "name_text_table", "name_text_key",
            ),
            "fork_type": ("fork_type", "fork_type_id", "name_zh", None, None),
            "equipment_item": (
                "equipment_item", "item_id", "name_zh",
                "name_text_table", "name_text_key",
            ),
            "equipment_suit": (
                "equipment_suit", "suit_id", "name_zh",
                "name_text_table", "name_text_key",
            ),
            "equipment_attribute": (
                "equipment_attribute", "attribute_id", "display_name_zh", None, None,
            ),
            "gameplay_ability": (
                "gameplay_ability_catalog", "ability_id", "name_zh",
                "name_text_table", "name_text_key",
            ),
            "monster": (
                "monster_catalog", "monster_manual_id", "name_zh", None, None,
            ),
            "clone_activity_category": (
                "clone_activity_category", "category_id", "name_zh", None, None,
            ),
            "clone_activity": (
                "clone_activity", "clone_id", "name_zh", None, None,
            ),
            "feast_stage": ("feast_stage", "stage_id", "name_zh", None, None),
        }
        catalog = catalogs.get(entity_kind)
        if catalog is None:
            return None
        table, id_column, name_column, table_column, key_column = catalog
        table_expression = (
            f"{table_column} AS name_text_table"
            if table_column is not None
            else "NULL AS name_text_table"
        )
        key_expression = (
            f"{key_column} AS name_text_key"
            if key_column is not None
            else "NULL AS name_text_key"
        )
        row = self._one(
            f"""SELECT {id_column} AS canonical_id,
                       {name_column} AS name_zh,
                       {table_expression}, {key_expression}
                FROM {table} WHERE {id_column} = ? COLLATE BINARY""",
            (stable_id,),
        )
        if row is None:
            return None
        name = str(row.get("name_zh") or "").strip()
        return self._record(
            entity_kind=entity_kind,
            canonical_id=str(row["canonical_id"]),
            names={"zh-CN": name} if name else {},
            text_table=row.get("name_text_table"),
            text_key=row.get("name_text_key"),
        )

    def _decode_names(self, raw_names: Any, *, description: str) -> dict[str, str]:
        try:
            names = json.loads(str(raw_names or "{}"))
        except json.JSONDecodeError as exc:
            self._raise_static_data_error(
                f"{description} 的本地化名称不是有效 JSON"
            )
        if not isinstance(names, dict):
            self._raise_static_data_error(
                f"{description} 的本地化名称必须是对象"
            )
        return {
            str(locale): str(name)
            for locale, name in names.items()
            if str(locale).strip() and str(name).strip()
        }

    @staticmethod
    def _record(
        *,
        entity_kind: str,
        canonical_id: str,
        names: dict[str, str],
        text_table: Any,
        text_key: Any,
    ) -> LocalizedTermRecord:
        return LocalizedTermRecord(
            entity_kind=entity_kind,
            canonical_id=canonical_id,
            names=names,
            text_table=str(text_table) if text_table is not None else None,
            text_key=str(text_key) if text_key is not None else None,
            source_kind="formal_localization" if names else "name_missing",
        )
