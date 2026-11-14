# 计算已保存配装与当前角色养成合并后的完整面板属性。
"""Qt-free saved-loadout attribute summary boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from src.domain.official_role.models import OfficialAttributeSummaryValue
from src.services.equipment.equipment_level_projection_service import project_equipment_items_to_max_level
from src.services.official_role.panel.attributes import calculate_official_role_attribute_summaries
from src.services.official_role.page import load_official_role_detail
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.integrations.static_catalog.static_catalog_release import StaticCatalogReadUnit


def load_saved_loadout_attribute_summaries(
    user_database_path: str | Path,
    static_database_path: str | Path,
    character_id: int,
    items: Sequence[Mapping[str, Any]],
    *,
    request_cache: dict[object, Any] | None = None,
    static_read_unit: StaticCatalogReadUnit | None = None,
) -> dict[str, tuple[OfficialAttributeSummaryValue, ...]]:
    """Combine one saved equipment slot with the current official role profile."""

    # 缓存只属于同次加载；角色详情跨槽位复用，装备仍逐方案投影，不缓存账号外的可变配置。
    key = None
    if static_read_unit is not None:
        static_read_unit.require_source(static_database_path, None, None)
        key = ('loadout_summary_detail', Path(user_database_path).resolve(),
               static_read_unit.release, int(character_id))
    detail = request_cache.get(key) if request_cache is not None and key is not None else None
    if detail is None:
        detail = load_official_role_detail(
            user_database_path,
            int(character_id),
            include_inventory_contexts=False,
            static_database_path=static_database_path,
            request_cache=request_cache,
            static_read_unit=static_read_unit,
        )
        if request_cache is not None and key is not None:
            request_cache[key] = detail
    if static_read_unit is not None:
        calculation_items = project_equipment_items_to_max_level(items, static_read_unit.dao)
    else:
        with StaticGameDataDao(static_database_path) as static_dao:
            calculation_items = project_equipment_items_to_max_level(items, static_dao)
    return calculate_official_role_attribute_summaries(detail, calculation_items)
