# 管理跨账号共享、随静态库更新覆盖的额外形状配置。
"""Read and update public extra-shape rules in the static game database."""

from __future__ import annotations

import re
import os
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from src.observability import OperationContext, operation_scope
from src.storage.sqlite.shared_data_dao import SharedDataDao
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


SHARED_DATABASE_ENV = "NTE_APP_SHARED_DB"


def resolve_shared_database(
    database_path: str | Path | None = None,
) -> Path:
    """解析不随账号切换的公共覆盖数据库路径。"""

    if database_path is not None:
        return Path(database_path).expanduser().resolve()
    configured = os.environ.get(SHARED_DATABASE_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "app_shared.sqlite3"


def _shape_grid_count(shape_label: str) -> int:
    match = re.search(r"(\d+)", str(shape_label or ""))
    grid_count = int(match.group(1)) if match is not None else 0
    if grid_count <= 0:
        raise ValueError(
            f"额外形状标签必须包含有效格数，例如 Type-3（当前值：{shape_label!r}）"
        )
    return grid_count


def get_effective_character_shape_bonus(
    static_dao: StaticGameDataDao,
    character_id: int,
    *,
    shared_database_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """优先返回本机公共覆盖，无覆盖时返回发行静态默认。"""

    raw_character_id = int(character_id)
    logical_key = static_dao.get_logical_character_key(raw_character_id)
    if logical_key:
        shared_path = resolve_shared_database(shared_database_path)
        override = None
        if shared_path.is_file():
            with SharedDataDao(shared_path) as shared_dao:
                override = shared_dao.get_shape_bonus_override(logical_key)
        if override is not None:
            attributes = {
                str(row["attribute_id"]): row
                for row in static_dao.list_equipment_attributes()
            }
            override["character_id"] = raw_character_id
            override["effective_source"] = "shared_override"
            override["properties"] = [
                {
                    **property_row,
                    **{
                        key: value
                        for key, value in (
                            attributes.get(str(property_row["property_id"])) or {}
                        ).items()
                        if key in {
                            "display_name_zh",
                            "filter_name_zh",
                            "show_percent",
                        }
                    },
                }
                for property_row in override.get("properties") or ()
            ]
            return override

    bundled = static_dao.get_character_shape_bonus(raw_character_id)
    if bundled is not None:
        bundled["effective_source"] = "static_default"
    return bundled


def save_public_character_shape_bonus(
    character_id: int,
    *,
    shape_label: str,
    property_values: Mapping[str, float],
    database_path: str | Path | None = None,
    shared_database_path: str | Path | None = None,
    operation_context: OperationContext | None = None,
) -> dict[str, Any]:
    """把一个逻辑角色的公共规则保存到独立覆盖库。"""

    operation = operation_context or OperationContext.create("basic_weight")
    with operation_scope(
        operation,
        started_event="shape_bonus.save_started",
        succeeded_event="shape_bonus.save_succeeded",
        failed_event="shape_bonus.save_failed",
        message="保存全部账号共享的额外形状覆盖",
        character_id=int(character_id),
        shape_label=str(shape_label or ""),
        property_count=len(property_values),
        property_ids=sorted(str(key) for key in property_values),
    ) as span:
        result = _save_public_character_shape_bonus(
            character_id,
            shape_label=shape_label,
            property_values=property_values,
            database_path=database_path,
            shared_database_path=shared_database_path,
        )
        span.annotate(effective_source=result.get("effective_source"))
        return result


def _save_public_character_shape_bonus(
    character_id: int,
    *,
    shape_label: str,
    property_values: Mapping[str, float],
    database_path: str | Path | None,
    shared_database_path: str | Path | None,
) -> dict[str, Any]:
    raw_character_id = int(character_id)
    if raw_character_id <= 0:
        raise ValueError("character_id 必须为正整数")
    if not isinstance(property_values, Mapping):
        raise ValueError("额外形状加成必须是属性映射")
    normalized_properties: list[tuple[str, float]] = []
    for property_id, raw_value in property_values.items():
        normalized_property_id = str(property_id or "").strip()
        value = float(raw_value)
        if not normalized_property_id or value < 0:
            raise ValueError("额外形状加成包含无效属性或数值")
        normalized_properties.append((normalized_property_id, value))

    with StaticGameDataDao(database_path) as static_dao:
        character = static_dao.get_character(raw_character_id)
        if character is None or not character.get("logical_character_key"):
            raise ValueError(f"公共静态数据库没有角色 {raw_character_id} 的逻辑角色标识")
        logical_key = str(character["logical_character_key"])
        bundled = static_dao.get_character_shape_bonus(raw_character_id) or {}
        known_properties = {
            str(row["attribute_id"])
            for row in static_dao.list_equipment_attributes()
        }
        unknown = sorted(
            property_id for property_id, _value in normalized_properties
            if property_id not in known_properties
        )
        if unknown:
            raise ValueError(f"额外形状加成包含未知官方属性：{'、'.join(unknown)}")
        dataset_id = str(
            static_dao.summary().get("dataset", {}).get("dataset_id") or ""
        )

    effective_label = str(shape_label or "").strip() or str(
        bundled.get("shape_label") or ""
    ).strip()
    grid_count = _shape_grid_count(effective_label)
    with SharedDataDao(resolve_shared_database(shared_database_path)) as shared_dao:
        shared_dao.upsert_shape_bonus_override(
            logical_key,
            representative_character_id=raw_character_id,
            shape_label=effective_label,
            shape_grid_count=grid_count,
            properties=[
                {
                    "property_id": property_id,
                    "display_value": value,
                }
                for property_id, value in normalized_properties
            ],
            based_on_dataset_id=dataset_id,
        )

    with StaticGameDataDao(database_path) as static_dao:
        result = get_effective_character_shape_bonus(
            static_dao,
            raw_character_id,
            shared_database_path=shared_database_path,
        )
    assert result is not None
    return result


def reset_public_character_shape_bonus(
    character_id: int,
    *,
    database_path: str | Path | None = None,
    shared_database_path: str | Path | None = None,
    operation_context: OperationContext | None = None,
) -> dict[str, Any] | None:
    """删除公共覆盖并立即恢复发行静态默认。"""

    operation = operation_context or OperationContext.create("basic_weight")
    with operation_scope(
        operation,
        started_event="shape_bonus.reset_started",
        succeeded_event="shape_bonus.reset_succeeded",
        failed_event="shape_bonus.reset_failed",
        message="恢复全部账号共享的额外形状发行默认值",
        character_id=int(character_id),
    ) as span:
        result = _reset_public_character_shape_bonus(
            character_id,
            database_path=database_path,
            shared_database_path=shared_database_path,
        )
        span.annotate(
            effective_source=(
                result.get("effective_source") if result is not None else None
            )
        )
        return result


def _reset_public_character_shape_bonus(
    character_id: int,
    *,
    database_path: str | Path | None,
    shared_database_path: str | Path | None,
) -> dict[str, Any] | None:
    raw_character_id = int(character_id)
    with StaticGameDataDao(database_path) as static_dao:
        logical_key = static_dao.get_logical_character_key(raw_character_id)
        if not logical_key:
            raise ValueError(f"公共静态数据库没有角色 {raw_character_id} 的逻辑角色标识")
        with SharedDataDao(resolve_shared_database(shared_database_path)) as shared_dao:
            shared_dao.delete_shape_bonus_override(logical_key)
        return get_effective_character_shape_bonus(
            static_dao,
            raw_character_id,
            shared_database_path=shared_database_path,
        )
