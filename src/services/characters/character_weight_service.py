# 初始化并维护账号 SQLite 中可编辑的角色权重。
"""账号权重优先，默认权重保持可刷新；批量提交复用完整校验和单一事务。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.observability import OperationContext, operation_scope
from src.domain.recommended_weights import base_weight_character_id
from src.domain.basic_weight import AccountWeightChange
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.characters.workshop_weight_template_service import (
    WorkshopWeightSnapshot,
    load_workshop_weight_snapshot,
)


def is_unmodified_account_weight_cache(record: Mapping[str, Any] | None) -> bool:
    """识别可刷新默认缓存，真正的账号显式编辑始终保留。"""

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
    *,
    workshop_snapshot: WorkshopWeightSnapshot | None = None,
) -> dict[str, Any] | None:
    """只读解析统一基础权重；不种默认记录、不改历史方案或边际结果。"""
    source_id = base_weight_character_id(character_id)
    existing = user_dao.get_character_weight_preferences(source_id) if user_dao is not None else None
    if existing is not None and not is_unmodified_account_weight_cache(existing):
        return _weight_projection(existing, character_id)
    snapshot = workshop_snapshot or load_workshop_weight_snapshot()
    recommended = snapshot.resolve(
        source_id, static_dao.get_character_recommended_weights(source_id),
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
        snapshot = load_workshop_weight_snapshot()
        public_revision = snapshot.revision or dataset_id
        result: dict[int, dict[str, Any]] = {}
        resolved: dict[int, dict[str, Any]] = {}
        for character_id in wanted_ids:
            source_id = base_weight_character_id(character_id)
            if source_id in resolved:
                result[character_id] = _weight_projection(resolved[source_id], character_id)
                continue
            recommended = snapshot.resolve(
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
    """原子保存账号基础权重；不修改静态默认值或历史方案评分。"""

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


def prepare_account_weight_change(
    user_dao: UserDataDao,
    static_dao: StaticGameDataDao,
    character_id: int,
    property_weights: Mapping[str, float],
    main_property_weights: Mapping[str, float] | None,
    *,
    workshop_snapshot: WorkshopWeightSnapshot | None = None,
) -> AccountWeightChange:
    """在调用方冻结的连接上只读准备；先校验全部字段，未改动不冻结公共默认值。"""
    source_id = base_weight_character_id(character_id)
    current = resolve_character_base_weights(
        user_dao, static_dao, character_id, workshop_snapshot=workshop_snapshot,
    ) or {}
    known = {str(row["attribute_id"]) for row in static_dao.list_equipment_attributes()}
    normalized = {str(pid): float(value) for pid, value in property_weights.items() if str(pid) in known}
    normalized_main = (
        {str(pid): float(value) for pid, value in main_property_weights.items() if str(pid) in known}
        if main_property_weights is not None else None
    )
    rows = []
    seen = set()
    for row in current.get("properties") or ():
        pid = str(row["property_id"])
        seen.add(pid)
        rows.append({"property_id": pid, "weight": normalized.get(pid, 0.0),
                     "main_weight": normalized_main.get(pid, 0.0) if normalized_main is not None
                                    else float(row.get("main_weight") or 0.0)})
    for pid in sorted(set(normalized) | set(normalized_main or {})):
        if pid not in seen:
            rows.append({"property_id": pid, "weight": normalized.get(pid, 0.0),
                         "main_weight": (normalized_main or {}).get(pid, 0.0)})
    validated = user_dao.validate_character_weight_rows(rows)
    return AccountWeightChange(
        character_id=source_id,
        source_dataset_id=str(static_dao.summary()["dataset"]["dataset_id"]),
        properties=tuple((row["property_id"], row["weight"], row["main_weight"]) for row in validated),
        changed=not current or not _same_weight_rows(current, rows),
    )


def apply_account_weight_change(user_dao: UserDataDao, change: AccountWeightChange) -> None:
    """应用已校验计划；DAO 内层只参与事务，整批的提交权仍属于调用方。"""
    if not change.changed:
        return
    rows = [{"property_id": pid, "weight": weight, "main_weight": main}
            for pid, weight, main in change.properties]
    if user_dao.get_character_weight_preferences(change.character_id) is None:
        user_dao.seed_character_weight_preferences(
            change.character_id, properties=rows, source_dataset_id=change.source_dataset_id,
            source_kind="account",
        )
    else:
        user_dao.save_character_weight_preferences(change.character_id, properties=rows)


def _save_account_character_weights(
    user_database_path: str | Path,
    character_id: int,
    property_weights: Mapping[str, float],
    *,
    main_property_weights: Mapping[str, float] | None,
    static_database_path: str | Path | None = None,
) -> dict[str, Any]:
    with (StaticGameDataDao(static_database_path) as static_dao,
          UserDataDao(user_database_path) as user_dao, user_dao.write_transaction()):
        change = prepare_account_weight_change(
            user_dao, static_dao, character_id, property_weights, main_property_weights,
        )
        apply_account_weight_change(user_dao, change)
        result = resolve_character_base_weights(user_dao, static_dao, character_id)
        assert result is not None
        stored = user_dao.get_character_weight_preferences(change.character_id)
        if stored is not None and not change.changed:
            # 只读默认投影没有种子时间；无改动保存仍返回原缓存元数据，不冒充账号编辑。
            result = {**stored, **result}
            if is_unmodified_account_weight_cache(stored):
                result["source_kind"] = "default"
        return result


def reset_account_character_weights(
    user_database_path: str | Path,
    character_ids: Iterable[int],
    *,
    operation_context: OperationContext | None = None,
    static_database_path: str | Path | None = None,
) -> dict[int, dict[str, Any]]:
    """同一账号事务恢复全部指定角色；任一步失败不留下部分重置。"""

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
    with (StaticGameDataDao(static_database_path) as static_dao, UserDataDao(user_database_path) as user_dao,
          user_dao.write_transaction()):
        dataset_id = str(static_dao.summary()["dataset"]["dataset_id"])
        snapshot = load_workshop_weight_snapshot()
        public_revision = snapshot.revision or dataset_id
        restored: dict[int, dict[str, Any]] = {}
        sources: dict[int, dict[str, Any]] = {}
        for character_id in wanted_ids:
            source_id = base_weight_character_id(character_id)
            if source_id in sources:
                restored[character_id] = _weight_projection(sources[source_id], character_id)
                continue
            recommended = snapshot.resolve(
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
