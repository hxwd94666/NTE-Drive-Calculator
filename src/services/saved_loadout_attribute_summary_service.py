# 计算已保存配装与当前角色养成合并后的完整面板属性。
"""Qt-free saved-loadout attribute summary boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from src.domain.official_role import OfficialAttributeSummaryValue
from src.services.equipment_level_projection_service import (
    project_equipment_items_to_max_level,
)
from src.services.official_role_page_service import (
    calculate_official_role_attribute_summaries,
    load_official_role_detail,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


def load_saved_loadout_attribute_summaries(
    user_database_path: str | Path,
    static_database_path: str | Path,
    character_id: int,
    items: Sequence[Mapping[str, Any]],
    *,
    request_cache: dict[object, Any] | None = None,
) -> dict[str, tuple[OfficialAttributeSummaryValue, ...]]:
    """Combine one saved equipment slot with the current official role profile."""

    detail = load_official_role_detail(
        user_database_path,
        int(character_id),
        include_inventory_contexts=False,
        static_database_path=static_database_path,
        request_cache=request_cache,
    )
    with StaticGameDataDao(static_database_path) as static_dao:
        calculation_items = project_equipment_items_to_max_level(items, static_dao)
    return calculate_official_role_attribute_summaries(detail, calculation_items)
