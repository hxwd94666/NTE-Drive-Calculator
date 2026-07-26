# 初始化并维护账号 SQLite 中可编辑的角色权重。
"""Account-scoped editable character weights."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


def is_unmodified_account_weight_cache(record: Mapping[str, Any] | None) -> bool:
    """Whether a private row is only a refreshable copy of public weights."""

    if not isinstance(record, Mapping):
        return False
    return (
        str(record.get("source_kind") or "") == "default"
        and str(record.get("seeded_at_utc") or "")
        == str(record.get("updated_at_utc") or "")
    )


def _same_weight_rows(
    existing: Mapping[str, Any], properties: Iterable[Mapping[str, Any]],
) -> bool:
    def normalized(rows: Iterable[Mapping[str, Any]]) -> list[tuple[str, float, float]]:
        return [
            (
                str(row.get("property_id") or ""),
                float(row.get("weight") or 0.0),
                float(row.get("main_weight") or 0.0),
            )
            for row in rows
        ]
    return normalized(existing.get("properties") or ()) == normalized(properties)


def ensure_account_character_weights(
    user_database_path: str | Path,
    character_ids: Iterable[int] | None = None,
) -> dict[int, dict[str, Any]]:
    """Refresh public defaults while preserving only genuine account edits.

    Public recommendations always live in ``game_static.sqlite3``.  The
    account database stores a refreshable ``default`` cache for untouched
    roles; saving a role changes its source to ``account`` and freezes it
    against later public-data updates.
    """

    with StaticGameDataDao() as static_dao, UserDataDao(user_database_path) as user_dao:
        wanted_ids = (
            [int(character_id) for character_id in character_ids]
            if character_ids is not None
            else [
                int(row["character_id"])
                for row in static_dao.list_role_template_characters()
            ]
        )
        dataset_id = str(static_dao.summary()["dataset"]["dataset_id"])
        result: dict[int, dict[str, Any]] = {}
        for character_id in wanted_ids:
            recommended = static_dao.get_character_recommended_weights(character_id)
            if recommended is None:
                continue
            properties = list(recommended.get("properties") or ())
            if not properties:
                continue
            existing = user_dao.get_character_weight_preferences(character_id)
            if existing is None:
                result[character_id] = user_dao.seed_character_weight_preferences(
                    character_id,
                    properties=properties,
                    source_dataset_id=dataset_id,
                    source_kind="default",
                )
            elif is_unmodified_account_weight_cache(existing):
                if (
                    str(existing.get("source_dataset_id") or "") == dataset_id
                    and _same_weight_rows(existing, properties)
                ):
                    result[character_id] = existing
                else:
                    refreshed = user_dao.refresh_unmodified_character_weight_preferences(
                        character_id,
                        properties=properties,
                        source_dataset_id=dataset_id,
                        source_kind="default",
                    )
                    result[character_id] = refreshed or existing
            else:
                result[character_id] = existing
        return result


def save_account_character_weights(
    user_database_path: str | Path,
    character_id: int,
    property_weights: Mapping[str, float],
    *,
    main_property_weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Persist the account SQLite weights without changing static recommendations."""

    current = ensure_account_character_weights(user_database_path, (character_id,)).get(
        int(character_id), {}
    )
    with StaticGameDataDao() as static_dao:
        known_property_ids = {
            str(row["attribute_id"]) for row in static_dao.list_equipment_attributes()
        }
        dataset_id = str(static_dao.summary()["dataset"]["dataset_id"])
    normalized = {
        str(property_id): float(weight)
        for property_id, weight in property_weights.items()
        if str(property_id) in known_property_ids and float(weight) >= 0
    }
    normalized_main = (
        {
            str(property_id): float(weight)
            for property_id, weight in main_property_weights.items()
            if str(property_id) in known_property_ids and float(weight) >= 0
        }
        if main_property_weights is not None
        else None
    )
    rows = []
    seen = set()
    for row in current.get("properties") or ():
        property_id = str(row["property_id"])
        seen.add(property_id)
        rows.append({
            "property_id": property_id,
            "weight": normalized.get(property_id, 0.0),
            "main_weight": (
                normalized_main.get(property_id, 0.0)
                if normalized_main is not None
                else float(row.get("main_weight") or 0.0)
            ),
        })
    for property_id in sorted(set(normalized) | set(normalized_main or {})):
        if property_id not in seen:
            rows.append({
                "property_id": property_id,
                "weight": normalized.get(property_id, 0.0),
                "main_weight": (normalized_main or {}).get(property_id, 0.0),
            })
    with UserDataDao(user_database_path) as user_dao:
        if not current:
            return user_dao.seed_character_weight_preferences(
                int(character_id),
                properties=rows,
                source_dataset_id=dataset_id,
                source_kind="account",
            )
        existing_rows = [
            (
                str(row["property_id"]), float(row.get("weight") or 0.0),
                float(row.get("main_weight") or 0.0),
            )
            for row in current.get("properties") or ()
        ]
        proposed_rows = [
            (
                str(row["property_id"]), float(row.get("weight") or 0.0),
                float(row.get("main_weight") or 0.0),
            )
            for row in rows
        ]
        # A no-op Save must remain a refreshable ``default`` row.  Otherwise
        # merely opening a form and pressing Save permanently blocks Workshop
        # updates for that character.
        if existing_rows == proposed_rows:
            return current
        return user_dao.save_character_weight_preferences(
            int(character_id), properties=rows
        )


def reset_account_character_weights(
    user_database_path: str | Path,
    character_ids: Iterable[int],
) -> dict[int, dict[str, Any]]:
    """Restore selected roles to current public defaults in one account DB."""

    wanted_ids = tuple(dict.fromkeys(int(character_id) for character_id in character_ids))
    if not wanted_ids:
        return {}
    with StaticGameDataDao() as static_dao, UserDataDao(user_database_path) as user_dao:
        dataset_id = str(static_dao.summary()["dataset"]["dataset_id"])
        restored: dict[int, dict[str, Any]] = {}
        for character_id in wanted_ids:
            recommended = static_dao.get_character_recommended_weights(character_id)
            if recommended is None or not recommended.get("properties"):
                continue
            restored[character_id] = user_dao.reset_character_weight_preferences_to_default(
                character_id,
                properties=list(recommended["properties"]),
                source_dataset_id=dataset_id,
            )
        return restored
