# 将静态推荐权重复制为账号独立、可编辑的角色权重。
"""Account-scoped editable copies of bundled character recommendations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


def _seed_rows(
    recommendation: Mapping[str, Any],
    known_property_ids: set[str],
) -> list[dict[str, Any]]:
    rows = []
    seen = set()
    for row in recommendation.get("properties") or ():
        property_id = str(row.get("property_id") or "")
        if property_id not in known_property_ids or property_id in seen:
            continue
        seen.add(property_id)
        rows.append({
            "property_id": property_id,
            "weight": float(row.get("weight") or 0.0),
            "main_weight": float(row.get("main_weight") or 0.0),
        })
    return rows


def ensure_account_character_weights(
    user_database_path: str | Path,
    character_ids: Iterable[int] | None = None,
) -> dict[int, dict[str, Any]]:
    """Seed missing account rows once; later bundled updates never overwrite user edits."""

    with StaticGameDataDao() as static_dao, UserDataDao(user_database_path) as user_dao:
        dataset = static_dao.summary()["dataset"]
        dataset_id = str(dataset["dataset_id"])
        known_property_ids = {
            str(row["attribute_id"]) for row in static_dao.list_equipment_attributes()
        }
        wanted_ids = (
            [int(character_id) for character_id in character_ids]
            if character_ids is not None
            else [
                int(row["character_id"])
                for row in static_dao.list_role_template_characters()
            ]
        )
        result = {}
        for character_id in wanted_ids:
            existing = user_dao.get_character_weight_preferences(character_id)
            if existing is not None:
                result[character_id] = existing
                continue
            recommendation = static_dao.get_character_recommended_weights(character_id)
            if recommendation is None:
                continue
            result[character_id] = user_dao.seed_character_weight_preferences(
                character_id,
                properties=_seed_rows(recommendation, known_property_ids),
                source_dataset_id=dataset_id,
                source_kind=str(recommendation.get("source_kind") or "default"),
            )
        return result


def save_account_character_weights(
    user_database_path: str | Path,
    character_id: int,
    property_weights: Mapping[str, float],
) -> dict[str, Any]:
    """Persist editable sub-stat weights while preserving bundled main-stat weights."""

    current = ensure_account_character_weights(user_database_path, (character_id,)).get(
        int(character_id)
    )
    if current is None:
        raise ValueError(f"角色 {character_id} 没有静态推荐权重")
    with StaticGameDataDao() as static_dao:
        known_property_ids = {
            str(row["attribute_id"]) for row in static_dao.list_equipment_attributes()
        }
    normalized = {
        str(property_id): float(weight)
        for property_id, weight in property_weights.items()
        if str(property_id) in known_property_ids and float(weight) >= 0
    }
    rows = []
    seen = set()
    for row in current.get("properties") or ():
        property_id = str(row["property_id"])
        seen.add(property_id)
        rows.append({
            "property_id": property_id,
            "weight": normalized.get(property_id, 0.0),
            "main_weight": float(row.get("main_weight") or 0.0),
        })
    for property_id in sorted(normalized):
        if property_id not in seen:
            rows.append({
                "property_id": property_id,
                "weight": normalized[property_id],
                "main_weight": 0.0,
            })
    with UserDataDao(user_database_path) as user_dao:
        return user_dao.save_character_weight_preferences(
            int(character_id), properties=rows
        )
