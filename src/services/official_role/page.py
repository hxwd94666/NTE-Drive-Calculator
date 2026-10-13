# 持有官方角色runtime页面读取与页签顺序owner，构建期使用独立借用入口。
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from time import perf_counter
from typing import Any

from src.domain.official_role.models import OFFICIAL_ROLE_TAB_ORDER_SCOPE
from src.integrations.official_role.read_inputs import _asset_root
from src.integrations.static_catalog_release import StaticCatalogReadUnit, static_catalog_read_unit
from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.storage.sqlite.user_data_dao import UserDataDao
from .detail_inputs import materialize_official_role_detail_inputs
from .detail_model import assemble_official_role_detail


def load_official_role_index(
    user_database_path: str | Path,
    *,
    asset_root: str | Path | None = None,
    stage_duration_ms: dict[str, float] | None = None,
    static_database_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """List official playable roles, ordered by account pointers when present."""

    started = perf_counter()

    def mark(stage: str) -> None:
        nonlocal started
        now = perf_counter()
        if stage_duration_ms is not None:
            stage_duration_ms[stage] = round((now - started) * 1000, 3)
        started = now

    catalog = GameUiAssetCatalog(_asset_root(asset_root))
    mark("asset_manifest")
    with static_catalog_read_unit(static_database_path) as read_unit, UserDataDao(user_database_path) as user_dao:
        static_dao = read_unit.dao
        mark("database_open")
        profiles = {row["character_id"]: row for row in user_dao.list_character_profiles()}
        mark("profiles")
        preferred_character_ids = [
            *user_dao.list_observed_character_ids(),
            *profiles,
        ]
        mark("observed_characters")
        characters = static_dao.list_role_template_characters(
            preferred_character_ids,
        )
        mark("static_characters")
        saved_order = user_dao.get_ui_item_order(OFFICIAL_ROLE_TAB_ORDER_SCOPE)
    mark("saved_order_and_close")
    saved_rank: dict[int, int] = {}
    for ordinal, item_key in enumerate(saved_order):
        try:
            character_id = int(item_key)
        except (TypeError, ValueError):
            continue
        saved_rank.setdefault(character_id, ordinal)
    result = sorted(
        [
            {
                **character,
                "icon_path": catalog.character_icon(int(character["character_id"])),
                "persisted": int(character["character_id"]) in profiles,
                "ordinal": profiles.get(int(character["character_id"]), {}).get("ordinal", 10000),
            }
            for character in characters
        ],
        key=lambda row: (
            0 if int(row["character_id"]) in saved_rank else 1,
            saved_rank.get(
                int(row["character_id"]),
                int(row["ordinal"]),
            ),
            int(row["character_id"]),
        ),
    )
    mark("icons_and_sort")
    return result


def save_official_role_tab_order(
    user_database_path: str | Path,
    character_ids: Sequence[int],
) -> list[int]:
    """Persist the character page's visual tab order for the current account."""

    normalized = [int(character_id) for character_id in character_ids]
    if any(character_id <= 0 for character_id in normalized):
        raise ValueError("角色 Tab 顺序包含无效 character_id")
    if len(set(normalized)) != len(normalized):
        raise ValueError("角色 Tab 顺序不能包含重复角色")
    with UserDataDao(user_database_path) as user_dao:
        user_dao.replace_ui_item_order(
            OFFICIAL_ROLE_TAB_ORDER_SCOPE,
            normalized,
        )
    return normalized


def load_official_role_detail(
    user_database_path: str | Path, character_id: int, *,
    asset_root: str | Path | None = None,
    include_inventory_contexts: bool = True,
    include_replacement_candidates: bool = True,
    static_database_path: str | Path | None = None,
    static_schema_version: int | None = None,
    shared_database_path: str | Path | None = None,
    request_cache: dict[object, Any] | None = None,
    static_read_unit: StaticCatalogReadUnit | None = None,
) -> dict[str, Any]:
    """正式manifest/pin/专属事务内物化，关闭后纯装配；失败不发布新缓存。"""
    pending_cache = dict(request_cache) if request_cache is not None else None
    with static_catalog_read_unit(static_database_path, expected_schema_version=static_schema_version,
                                  read_unit=static_read_unit) as read_unit:
        with UserDataDao(user_database_path) as user_dao:
            inputs = materialize_official_role_detail_inputs(
                read_unit.dao, character_id, user_dao=user_dao, asset_root=asset_root,
                include_inventory_contexts=include_inventory_contexts,
                include_replacement_candidates=include_replacement_candidates,
                shared_database_path=shared_database_path, request_cache=pending_cache,
                cache_identity=read_unit.release,
            )
        release = read_unit.release
    detail = assemble_official_role_detail(inputs)
    # 只有runtime发行owner可附已验证token，构建纯detail没有此字段。
    detail["static_release"] = release
    if request_cache is not None:
        request_cache.update(pending_cache or {})
    return detail
