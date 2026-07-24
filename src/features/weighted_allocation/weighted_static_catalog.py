# 为词条配装页面缓存公共静态目录，避免反复读取整套官方资料。
"""Process-local static catalogue used by the weighted allocation page.

The catalogue contains only immutable data from the public game database and
game-ui asset directory.  Account data, snapshots and preferences deliberately
remain outside it so account switching cannot leak state between users.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao, resolve_static_database


@dataclass(frozen=True, slots=True)
class WeightedStaticCatalog:
    """Static values needed to build the role selector and result cards."""

    characters: tuple[dict[str, Any], ...]
    plans_by_character_id: dict[int, dict[str, Any]]
    attributes: tuple[dict[str, Any], ...]
    suits: tuple[dict[str, Any], ...]
    equipment_items: tuple[dict[str, Any], ...]
    item_icons: dict[str, Path | None]


_CACHE_LOCK = RLock()
_CACHE: dict[tuple[str, int, int, str], WeightedStaticCatalog] = {}


def _cache_key(asset_root: str | Path) -> tuple[str, int, int, str]:
    database_path = resolve_static_database()
    stat = database_path.stat()
    return (
        str(database_path),
        int(stat.st_mtime_ns),
        int(stat.st_size),
        str(Path(asset_root).resolve()),
    )


def get_weighted_static_catalog(asset_root: str | Path) -> WeightedStaticCatalog:
    """Load static data once, invalidating automatically after DB replacement."""

    key = _cache_key(asset_root)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached
        # A static-data refresh replaces the SQLite file.  Drop the old entry
        # rather than retaining multiple complete catalogues in a long session.
        _CACHE.clear()
        with StaticGameDataDao() as dao:
            plans = dao.list_equipment_plans()
            equipment_items = tuple(dao.list_equipment_items())
            catalog = GameUiAssetCatalog(Path(asset_root))
            value = WeightedStaticCatalog(
                characters=tuple(dao.list_characters()),
                plans_by_character_id={
                    int(plan["character_id"]): plan for plan in plans
                },
                attributes=tuple(dao.list_equipment_attributes()),
                suits=tuple(dao.list_suits()),
                equipment_items=equipment_items,
                item_icons={
                    str(row["item_id"]): catalog.inventory_item_icon(
                        str(row.get("kind") or ""), str(row["item_id"]),
                    )
                    for row in equipment_items
                },
            )
        _CACHE[key] = value
        return value


def clear_weighted_static_catalog_cache() -> None:
    """Test and data-refresh hook for explicit cache invalidation."""

    with _CACHE_LOCK:
        _CACHE.clear()
