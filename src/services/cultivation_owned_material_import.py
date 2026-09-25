# 将账号原生物品归档投影为养成计算器本次草稿的已观测材料数量。
"""Read-only, account-bound projection; absence never represents owned zero."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from src.domain.all_item_snapshot import validate_all_item_snapshot
from src.storage.sqlite.all_item_snapshot_dao import read_latest_all_item_snapshot_archive
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


_MATERIAL_SOURCE = "InventoryContainerMap.InventoryItemsMap"
_MAX_INPUT_QUANTITY = 99_999_999


@dataclass(frozen=True, slots=True)
class ImportedOwnedMaterials:
    quantities: tuple[tuple[str, int], ...]
    saved_at_utc: str
    snapshot_id: int
    skipped_item_count: int


def project_observed_materials(
    snapshot: dict, progression_ids: frozenset[str]
) -> tuple[dict[str, int], int]:
    """Accept only unambiguous HTItem amounts from the formal material container."""

    validate_all_item_snapshot(snapshot)
    quantities: dict[str, int] = {}
    ambiguous: set[str] = set()
    for row in snapshot["records"]:
        if row.get("kind") != "HTItem":
            continue
        raw_item_id = row.get("ItemID")
        if not isinstance(raw_item_id, str):
            continue
        item_id = "Gold" if raw_item_id == "gold" else raw_item_id
        if item_id not in progression_ids:
            continue
        amount = row.get("Amount")
        if (row.get("source") != _MATERIAL_SOURCE
                or type(amount) is not int or not 0 <= amount <= _MAX_INPUT_QUANTITY
                or row.get("bIsTemporary") is not False
                or row.get("mapUidMatchesItem") is not True
                or row.get("duplicateMapUidObserved") is not False
                or not isinstance(row.get("UniqueID"), dict)
                or row.get("mapUniqueID") != row["UniqueID"]
                or item_id in quantities):
            ambiguous.add(item_id)
            continue
        quantities[item_id] = amount
    for item_id in ambiguous:
        quantities.pop(item_id, None)
    return quantities, len(ambiguous)


class CultivationOwnedMaterialImportService:
    """Freeze account and static dataset at construction; never write either database."""

    def __init__(
        self, *, user_database_path: str | Path, static_database_path: str | Path,
        account_id: str,
    ) -> None:
        self._user_database_path = Path(user_database_path)
        self._static_database_path = Path(static_database_path)
        self._account_id = str(account_id)

    def load_latest(self) -> ImportedOwnedMaterials:
        archive = read_latest_all_item_snapshot_archive(
            self._user_database_path, account_id=self._account_id
        )
        if archive is None:
            raise ValueError("当前账号尚无原生物品归档；请先完成原生背包同步。")
        if archive["source"] != "nte_core":
            raise ValueError("物品归档的来源不是原生同步。")
        raw = archive["raw_snapshot_json"]
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != archive["content_sha256"]:
            raise ValueError("物品归档内容校验失败；请重新执行原生背包同步。")
        snapshot = json.loads(raw)
        with StaticGameDataDao(self._static_database_path) as static_dao:
            ids = static_dao.progression_item_ids()
        quantities, skipped = project_observed_materials(snapshot, ids)
        return ImportedOwnedMaterials(
            quantities=tuple(sorted(quantities.items())),
            saved_at_utc=str(archive["saved_at_utc"]),
            snapshot_id=int(archive["snapshot_id"]),
            skipped_item_count=skipped,
        )


__all__ = [
    "CultivationOwnedMaterialImportService", "ImportedOwnedMaterials",
    "project_observed_materials",
]
