# 从官方 SQLite 快照取出一件装备，交给现有鉴定流程评分。
"""Load one official snapshot item for the existing identification pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.models.equipment import Drive, Tape
from src.services.sqlite_allocation_inventory import SqliteAllocationInventory
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


class WarehouseIdentificationError(RuntimeError):
    """The selected warehouse card cannot be loaded from its fixed snapshot."""


class WarehouseIdentificationService:
    """Read one fixed snapshot item without reintroducing a JSON inventory path."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        dao_factory: Callable[..., UserDataDao] = UserDataDao,
        static_dao_factory: Callable[..., StaticGameDataDao] = StaticGameDataDao,
        projection_factory: Callable[..., SqliteAllocationInventory] = SqliteAllocationInventory,
    ) -> None:
        self.database_path = Path(database_path)
        self.dao_factory = dao_factory
        self.static_dao_factory = static_dao_factory
        self.projection_factory = projection_factory

    def load_item(self, snapshot_id: int, uid: str) -> Drive | Tape:
        if not isinstance(snapshot_id, int) or snapshot_id <= 0:
            raise WarehouseIdentificationError("没有可鉴定的稳定背包快照")
        with self.dao_factory(self.database_path) as user_dao, self.static_dao_factory() as static_dao:
            projection = self.projection_factory(user_dao, static_dao).build(snapshot_id)
        payload = next((entry for entry in projection.items if entry["uid"] == uid), None)
        if payload is None:
            raise WarehouseIdentificationError("该装备已不在当前稳定背包快照中")
        return Drive(**payload) if payload["item_type"] == "drive" else Tape(**payload)
