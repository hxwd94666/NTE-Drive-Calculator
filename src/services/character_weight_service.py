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
        return user_dao.save_character_weight_preferences(
            int(character_id), properties=rows
        )


def save_account_character_shape_bonus(
    user_database_path: str | Path,
    character_id: int,
    *,
    shape_label: str,
    property_values: Mapping[str, float],
) -> dict[str, Any]:
    """Persist an account-local override of a role's extra shape bonus."""

    with StaticGameDataDao() as static_dao:
        known_property_ids = {
            str(row["attribute_id"])
            for row in static_dao.list_equipment_attributes()
        }
    normalized = {
        str(property_id): float(value)
        for property_id, value in property_values.items()
        if str(property_id) in known_property_ids
    }
    if len(normalized) != len(property_values):
        raise ValueError("额外形状加成包含未知官方属性")
    with UserDataDao(user_database_path) as user_dao:
        return user_dao.save_character_shape_bonus_preferences(
            int(character_id),
            shape_label=shape_label,
            property_values=normalized,
        )
