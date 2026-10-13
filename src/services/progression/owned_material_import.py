# 将账号已保存的原生归档或抓包观测投影为养成草稿材料数量。
"""Read-only, account-bound projection; absence never represents owned zero."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.domain.all_item_snapshot import validate_all_item_snapshot
from src.storage.sqlite.inventory.all_item_snapshot_dao import read_latest_all_item_snapshot_archive
from src.storage.sqlite.inventory.packet_item_observation_dao import read_latest_packet_item_observation
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


_MATERIAL_SOURCE = "InventoryContainerMap.InventoryItemsMap"
_MAX_INPUT_QUANTITY = 99_999_999


@dataclass(frozen=True, slots=True)
class ImportedOwnedMaterials:
    quantities: tuple[tuple[str, int], ...]
    saved_at_utc: str
    snapshot_id: int | None
    skipped_item_count: int
    source: str = "native"


def project_packet_materials(
    snapshot: dict[str, Any], progression_ids: frozenset[str]
) -> tuple[dict[str, int], int]:
    """Project only valid packet observations; absent IDs remain unknown."""

    rows = snapshot.get("items")
    count = snapshot.get("item_count")
    if (snapshot.get("source") != "packet" or snapshot.get("complete") is not False
            or type(snapshot.get("generation")) is not int or snapshot["generation"] < 1
            or type(snapshot.get("observed_at_unix_ms")) is not int
            or not 1 <= snapshot["observed_at_unix_ms"] <= 253_402_300_799_999
            or not isinstance(rows, list) or type(count) is not int
            or not 1 <= count <= 10_000 or len(rows) != count):
        raise ValueError("抓包材料观测尚未就绪或数据不完整，请保持低风险同步运行后重试。")
    quantities: dict[str, int] = {}
    ambiguous: set[str] = set()
    seen_uids: set[tuple[int, int]] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("抓包材料观测包含无效物品记录。")
        raw_id = row.get("item_id")
        if not isinstance(raw_id, str):
            continue
        item_id = "Gold" if raw_id == "gold" else raw_id
        if item_id not in progression_ids:
            continue
        uid = row.get("uid")
        amount = row.get("amount")
        valid_uid = (isinstance(uid, dict)
                     and all(type(uid.get(key)) is int and 0 <= uid[key] <= 0xFFFF_FFFF
                             for key in ("slot", "serial")))
        if (not valid_uid or type(amount) is not int
                or not 0 <= amount <= _MAX_INPUT_QUANTITY or item_id in quantities):
            ambiguous.add(item_id)
            continue
        uid_pair = (uid["slot"], uid["serial"])
        if uid_pair in seen_uids:
            raise ValueError("抓包材料观测包含重复物品 UID，请重新同步。")
        seen_uids.add(uid_pair)
        quantities[item_id] = amount
    for item_id in ambiguous:
        quantities.pop(item_id, None)
    return quantities, len(ambiguous)


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

    def load_packet_observations(self, snapshot: dict[str, Any]) -> ImportedOwnedMaterials:
        with StaticGameDataDao(self._static_database_path) as static_dao:
            ids = static_dao.progression_item_ids()
        quantities, skipped = project_packet_materials(snapshot, ids)
        observed_at = datetime.fromtimestamp(
            snapshot["observed_at_unix_ms"] / 1000, tz=timezone.utc
        ).isoformat(timespec="milliseconds")
        return ImportedOwnedMaterials(
            quantities=tuple(sorted(quantities.items())), saved_at_utc=observed_at,
            snapshot_id=None, skipped_item_count=skipped, source="packet",
        )

    def load_latest_packet(self) -> ImportedOwnedMaterials:
        row = read_latest_packet_item_observation(
            self._user_database_path, account_id=self._account_id
        )
        if row is None:
            raise ValueError("当前账号尚无已保存的抓包材料观测；请先运行低风险背包同步并等待数据稳定。")
        return self.load_packet_observations(row["snapshot"])


__all__ = [
    "CultivationOwnedMaterialImportService", "ImportedOwnedMaterials",
    "project_observed_materials",
    "project_packet_materials",
]
