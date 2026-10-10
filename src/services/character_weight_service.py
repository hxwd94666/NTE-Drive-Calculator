# 初始化并维护账号 SQLite 中可编辑的角色权重。
"""Account-scoped editable character weights."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.observability import OperationContext, operation_scope
from src.domain.recommended_weights import base_weight_character_id
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.workshop_weight_template_service import (
    effective_workshop_recommended_weights,
    workshop_weight_template_revision,
)


def is_unmodified_account_weight_cache(record: Mapping[str, Any] | None) -> bool:
    """Whether a private row is only a refreshable copy of public weights."""

    if not isinstance(record, Mapping):
        return False
    return (
        str(record.get("source_kind") or "") == "default"
        and str(record.get("seeded_at_utc") or "")
        == str(record.get("updated_at_utc") or "")
    )


def _weight_projection(record: Mapping[str, Any], character_id: int) -> dict[str, Any]:
    """保留消费者的角色身份，同时明确基础权重的独立来源。"""
    return {
        **deepcopy(dict(record)),
        "character_id": int(character_id),
        "weight_source_character_id": base_weight_character_id(character_id),
    }


def resolve_character_base_weights(
    user_dao: UserDataDao | None,
    static_dao: StaticGameDataDao,
    character_id: int,
) -> dict[str, Any] | None:
    """只读解析统一基础权重；不种默认记录、不改历史方案或边际结果。"""
    source_id = base_weight_character_id(character_id)
    existing = user_dao.get_character_weight_preferences(source_id) if user_dao is not None else None
    if existing is not None and not is_unmodified_account_weight_cache(existing):
        return _weight_projection(existing, character_id)
    recommended = effective_workshop_recommended_weights(
        None, source_id, static_dao.get_character_recommended_weights(source_id),
    )
    record = recommended if recommended is not None else existing
    return _weight_projection(record, character_id) if record is not None else None


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
    *,
    static_database_path: str | Path | None = None,
    persist_defaults: bool = True,
) -> dict[int, dict[str, Any]]:
    """Refresh public defaults while preserving only genuine account edits.

    Public recommendations resolve from the startup-refreshed Workshop template
    and then the packaged static fallback.  The account database stores a
    refreshable ``default`` cache for untouched
    roles; saving a role changes its source to ``account`` and freezes it
    against later public-data updates.
    Read-only editor requests resolve the same defaults with ``persist_defaults=False``
    and never create or refresh an account cache as a side effect of opening a page.
    """

    with (StaticGameDataDao(static_database_path) as static_dao, UserDataDao(user_database_path) as user_dao,
          user_dao.read_consistent_state() if not persist_defaults else nullcontext()):
        wanted_ids = (
            [int(character_id) for character_id in character_ids]
            if character_ids is not None
            else [
                int(row["character_id"])
                for row in static_dao.list_role_template_characters()
            ]
        )
        dataset_id = str(static_dao.summary()["dataset"]["dataset_id"])
        public_revision = workshop_weight_template_revision() or dataset_id
        result: dict[int, dict[str, Any]] = {}
        resolved: dict[int, dict[str, Any]] = {}
        for character_id in wanted_ids:
            source_id = base_weight_character_id(character_id)
            if source_id in resolved:
                result[character_id] = _weight_projection(resolved[source_id], character_id)
                continue
            recommended = effective_workshop_recommended_weights(
                None,
                source_id,
                static_dao.get_character_recommended_weights(source_id),
            )
            existing = user_dao.get_character_weight_preferences(source_id)
            properties = list((recommended or {}).get("properties") or ())
            if existing is not None and not is_unmodified_account_weight_cache(existing):
                resolved[source_id] = existing
                result[character_id] = _weight_projection(existing, character_id)
                continue
            if not properties:
                if existing is not None:
                    resolved[source_id] = existing
                    result[character_id] = _weight_projection(existing, character_id)
                continue
            if not persist_defaults and (existing is None or is_unmodified_account_weight_cache(existing)):
                # An editor read resolves defaults, but must not become a late account write.
                resolved[source_id] = {
                    "source_kind": "default", "source_dataset_id": public_revision,
                    "properties": properties,
                    "property_weights": {str(row["property_id"]): float(row["weight"]) for row in properties if float(row.get("weight") or 0) > 0},
                    "main_property_weights": {str(row["property_id"]): float(row["main_weight"]) for row in properties if float(row.get("main_weight") or 0) > 0},
                }
                result[character_id] = _weight_projection(resolved[source_id], character_id)
                continue
            if existing is None:
                resolved[source_id] = user_dao.seed_character_weight_preferences(
                    source_id,
                    properties=properties,
                    source_dataset_id=public_revision,
                    source_kind="default",
                )
            elif is_unmodified_account_weight_cache(existing):
                if (
                    str(existing.get("source_dataset_id") or "") == public_revision
                    and _same_weight_rows(existing, properties)
                ):
                    resolved[source_id] = existing
                else:
                    refreshed = user_dao.refresh_unmodified_character_weight_preferences(
                        source_id,
                        properties=properties,
                        source_dataset_id=public_revision,
                        source_kind="default",
                    )
                    resolved[source_id] = refreshed or existing
            else:
                resolved[source_id] = existing
            result[character_id] = _weight_projection(resolved[source_id], character_id)
        return result


def save_account_character_weights(
    user_database_path: str | Path,
    character_id: int,
    property_weights: Mapping[str, float],
    *,
    main_property_weights: Mapping[str, float] | None = None,
    operation_context: OperationContext | None = None,
    static_database_path: str | Path | None = None,
) -> dict[str, Any]:
    """Persist the account SQLite weights without changing static recommendations."""

    operation = operation_context or OperationContext.create("basic_weight")
    with operation_scope(
        operation,
        started_event="basic_weight.save_started",
        succeeded_event="basic_weight.save_succeeded",
        failed_event="basic_weight.save_failed",
        message="保存当前账号角色基础权重",
        character_id=int(character_id),
        property_count=len(property_weights),
        main_property_count=len(main_property_weights or {}),
    ) as span:
        result = _save_account_character_weights(
            user_database_path,
            character_id,
            property_weights,
            main_property_weights=main_property_weights,
            static_database_path=static_database_path,
        )
        span.annotate(
            source_kind=str(result.get("source_kind") or ""),
            property_ids=sorted(str(key) for key in property_weights),
            main_property_ids=sorted(str(key) for key in (main_property_weights or {})),
        )
        return _weight_projection(result, character_id)


def _save_account_character_weights(
    user_database_path: str | Path,
    character_id: int,
    property_weights: Mapping[str, float],
    *,
    main_property_weights: Mapping[str, float] | None,
    static_database_path: str | Path | None = None,
) -> dict[str, Any]:
    character_id = base_weight_character_id(character_id)
    current = ensure_account_character_weights(
        user_database_path, (character_id,), static_database_path=static_database_path,
    ).get(
        int(character_id), {}
    )
    # Account-created roles deliberately have no static recommendation.  Their
    # seed is still a normal account-owned weight record and must be editable.
    if not current:
        with UserDataDao(user_database_path) as user_dao:
            current = user_dao.get_character_weight_preferences(int(character_id)) or {}
    with StaticGameDataDao(static_database_path) as static_dao:
        known_property_ids = {
            str(row["attribute_id"]) for row in static_dao.list_equipment_attributes()
        }
        dataset_id = str(static_dao.summary()["dataset"]["dataset_id"])
        public_revision = workshop_weight_template_revision() or dataset_id
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
    *,
    operation_context: OperationContext | None = None,
    static_database_path: str | Path | None = None,
) -> dict[int, dict[str, Any]]:
    """Restore selected roles to current public defaults in one account DB."""

    wanted_ids = tuple(dict.fromkeys(int(character_id) for character_id in character_ids))
    if not wanted_ids:
        return {}
    operation = operation_context or OperationContext.create("basic_weight")
    with operation_scope(
        operation,
        started_event="basic_weight.reset_started",
        succeeded_event="basic_weight.reset_succeeded",
        failed_event="basic_weight.reset_failed",
        message="恢复当前账号角色基础权重默认值",
        requested_character_count=len(wanted_ids),
    ) as span:
        restored = _reset_account_character_weights(
            user_database_path,
            wanted_ids,
            static_database_path=static_database_path,
        )
        span.annotate(
            restored_character_count=len(restored),
            character_ids=sorted(restored),
        )
        return restored


def _reset_account_character_weights(
    user_database_path: str | Path,
    wanted_ids: tuple[int, ...],
    *,
    static_database_path: str | Path | None = None,
) -> dict[int, dict[str, Any]]:
    with StaticGameDataDao(static_database_path) as static_dao, UserDataDao(user_database_path) as user_dao:
        dataset_id = str(static_dao.summary()["dataset"]["dataset_id"])
        public_revision = workshop_weight_template_revision() or dataset_id
        restored: dict[int, dict[str, Any]] = {}
        sources: dict[int, dict[str, Any]] = {}
        for character_id in wanted_ids:
            source_id = base_weight_character_id(character_id)
            if source_id in sources:
                restored[character_id] = _weight_projection(sources[source_id], character_id)
                continue
            recommended = effective_workshop_recommended_weights(
                None,
                source_id,
                static_dao.get_character_recommended_weights(source_id),
            )
            if recommended is None or not recommended.get("properties"):
                continue
            sources[source_id] = user_dao.reset_character_weight_preferences_to_default(
                source_id,
                properties=list(recommended["properties"]),
                source_dataset_id=public_revision,
            )
            restored[character_id] = _weight_projection(sources[source_id], character_id)
        return restored
