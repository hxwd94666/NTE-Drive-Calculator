# 官方角色额外形状只读静态库；旧公共覆盖仅保留清理能力。
"""Resolve immutable official extra-shape rules from release static data."""

from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from src.observability import OperationContext, operation_scope
from src.storage.sqlite.shared_data_dao import SharedDataDao
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


SHARED_DATABASE_ENV = "NTE_APP_SHARED_DB"
DEFAULT_EXTRA_SHAPE_LABEL = "Type-3"


def resolve_shared_database(
    database_path: str | Path | None = None,
) -> Path:
    """解析不随账号切换的公共覆盖数据库路径。"""

    if database_path is not None:
        return Path(database_path).expanduser().resolve()
    configured = os.environ.get(SHARED_DATABASE_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "data" / "app_shared.sqlite3"


def get_effective_character_shape_bonus(
    static_dao: StaticGameDataDao,
    character_id: int,
    *,
    shared_database_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Return the release-static rule; legacy shared overrides never participate."""

    raw_character_id = int(character_id)
    del shared_database_path
    bundled = static_dao.get_character_shape_bonus(raw_character_id)
    if bundled is not None:
        bundled["effective_source"] = "static_default"
    return bundled


def character_shape_profile_fields(
    shape_bonus: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project one immutable shape rule into a battle/profile JSON payload."""

    shape_bonus = shape_bonus or {}
    return {
        "extra_shape_label": str(shape_bonus.get("shape_label") or ""),
        "extra_shape_buffs": {
            str(row["property_id"]): float(row["display_value"])
            for row in shape_bonus.get("properties") or ()
        },
        "extra_shape_source": "static_database",
    }


def static_character_shape_profile_fields(
    static_dao: StaticGameDataDao,
    character_id: int,
) -> dict[str, Any]:
    return character_shape_profile_fields(
        static_dao.get_character_shape_bonus(int(character_id))
    )


def save_public_character_shape_bonus(
    character_id: int,
    *,
    shape_label: str,
    property_values: Mapping[str, float],
    database_path: str | Path | None = None,
    shared_database_path: str | Path | None = None,
    operation_context: OperationContext | None = None,
) -> dict[str, Any]:
    """Reject the removed official-role override write boundary."""

    operation = operation_context or OperationContext.create("basic_weight")
    with operation_scope(
        operation,
        started_event="shape_bonus.save_started",
        succeeded_event="shape_bonus.save_succeeded",
        failed_event="shape_bonus.save_failed",
        message="拒绝修改官方角色额外形状",
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
    del shape_label, property_values, shared_database_path
    with StaticGameDataDao(database_path) as static_dao:
        character = static_dao.get_character(raw_character_id)
    if character is not None:
        raise ValueError(
            f"官方角色 {raw_character_id} 的额外形状由静态资源库管理，不可编辑"
        )
    raise ValueError("额外形状只允许通过自创角色保存入口编辑")


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
        message="清理旧版官方额外形状公共覆盖",
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
