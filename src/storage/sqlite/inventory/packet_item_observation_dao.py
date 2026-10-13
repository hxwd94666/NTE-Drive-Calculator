# 保存当前账号的稳定抓包物品观测，不触碰完整背包当前指针。
"""Immutable packet item observations with account-bound, read-only retrieval."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

from src.storage.sqlite.protocols import UserDataDaoMixinHost
from src.storage.sqlite.user_data_support import (
    DEFAULT_SNAPSHOT_RETENTION_COUNT, UserDataError, UserDataValidationError, _utc_now,
)


def normalize_packet_item_observation(value: object) -> dict[str, Any]:
    """Validate the bounded, cumulative Core event before it enters SQLite."""

    if not isinstance(value, Mapping):
        raise UserDataValidationError("抓包物品观测不是对象")
    rows = value.get("items")
    count = value.get("item_count")
    generation = value.get("generation")
    sequence = value.get("sequence")
    observed_ms = value.get("observed_at_unix_ms")
    if (value.get("source") != "packet" or value.get("complete") is not False
            or type(generation) is not int or not 1 <= generation <= 2**63 - 1
            or type(sequence) is not int or not 1 <= sequence <= 2**63 - 1
            or type(observed_ms) is not int or not 1 <= observed_ms <= 253_402_300_799_999
            or type(count) is not int or not 1 <= count <= 10_000
            or not isinstance(rows, list) or len(rows) != count):
        raise UserDataValidationError("抓包物品观测的来源、版本或件数无效")
    items: list[dict[str, Any]] = []
    seen_uids: set[tuple[int, int]] = set()
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("uid"), Mapping):
            raise UserDataValidationError("抓包物品观测包含无效物品")
        uid = row["uid"]
        slot, serial = uid.get("slot"), uid.get("serial")
        item_id, amount = row.get("item_id"), row.get("amount")
        if (type(slot) is not int or not 0 <= slot <= 0xFFFF_FFFF
                or type(serial) is not int or not 0 <= serial <= 0xFFFF_FFFF
                or not isinstance(item_id, str) or not 1 <= len(item_id) <= 256
                or any(ord(char) < 32 for char in item_id)
                or type(amount) is not int or not 0 <= amount <= 2**63 - 1):
            raise UserDataValidationError("抓包物品观测包含无效 ID 或数量")
        pair = (slot, serial)
        if pair in seen_uids:
            raise UserDataValidationError("抓包物品观测包含重复 UID")
        seen_uids.add(pair)
        items.append({"uid": {"slot": slot, "serial": serial}, "item_id": item_id, "amount": amount})
    items.sort(key=lambda item: (item["uid"]["slot"], item["uid"]["serial"]))
    result = {
        "source": "packet", "complete": False, "generation": generation,
        "sequence": sequence, "observed_at_unix_ms": observed_ms,
        "item_count": count, "items": items,
    }
    raw = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(raw.encode("utf-8")) > 4_000_000:
        raise UserDataValidationError("抓包物品观测超过大小上限")
    return result


def read_latest_packet_item_observation(database_path: str | Path, *, account_id: str):
    """Read saved observations without migrating or writing the account database."""

    uri = f"{Path(database_path).expanduser().resolve().as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            profile = connection.execute(
                "SELECT account_id FROM database_profile WHERE singleton_id = 1"
            ).fetchone()
            if profile is None or profile["account_id"] != account_id:
                raise UserDataValidationError("抓包材料观测与当前账号不一致")
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='packet_item_observation'"
            ).fetchone() is None:
                return None
            row = connection.execute(
                "SELECT observation_id, inventory_snapshot_id, generation, sequence, "
                "observed_at_unix_ms, item_count, raw_snapshot_json, content_sha256, saved_at_utc "
                "FROM packet_item_observation ORDER BY observation_id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            raw = row["raw_snapshot_json"]
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > 4_000_000:
                raise UserDataValidationError("抓包材料观测超过大小上限")
            if hashlib.sha256(raw.encode("utf-8")).hexdigest() != row["content_sha256"]:
                raise UserDataValidationError("抓包材料观测内容校验失败")
            try:
                snapshot = normalize_packet_item_observation(json.loads(raw))
            except (ValueError, TypeError) as exc:
                raise UserDataValidationError("抓包材料观测格式无效") from exc
            for field in ("generation", "sequence", "observed_at_unix_ms", "item_count"):
                if row[field] != snapshot[field]:
                    raise UserDataValidationError("抓包材料观测元数据不一致")
            return {
                "observation_id": row["observation_id"],
                "inventory_snapshot_id": row["inventory_snapshot_id"],
                "saved_at_utc": row["saved_at_utc"], "snapshot": snapshot,
            }
    except sqlite3.Error as exc:
        raise UserDataError("读取抓包材料观测失败") from exc


class PacketItemObservationDaoMixin(UserDataDaoMixinHost):
    """单独保存抓包物品观测，不提升为原生角色实例或装备写入能力。"""

    def save_packet_item_observation(
        self, observation: Mapping[str, Any], *, account_id: str,
        check: Callable[[], None], inventory_snapshot_id: int | None = None,
    ) -> int:
        check()
        if not account_id or self.profile()["account_id"] != account_id:
            raise UserDataValidationError("抓包材料观测的账号身份不符")
        snapshot = normalize_packet_item_observation(observation)
        raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        connection = self._db()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                check()
                if inventory_snapshot_id is not None:
                    current = connection.execute(
                        "SELECT snapshot_id FROM inventory_snapshot WHERE is_current = 1"
                    ).fetchone()
                    if current is None or current["snapshot_id"] != inventory_snapshot_id:
                        raise UserDataValidationError("关联的完整背包快照已经变化")
                previous = connection.execute(
                    "SELECT observation_id, content_sha256 FROM packet_item_observation "
                    "ORDER BY observation_id DESC LIMIT 1"
                ).fetchone()
                if previous is not None and previous["content_sha256"] == digest:
                    check()
                    return int(previous["observation_id"])
                cursor = connection.execute(
                    "INSERT INTO packet_item_observation(inventory_snapshot_id, generation, "
                    "sequence, observed_at_unix_ms, item_count, raw_snapshot_json, "
                    "content_sha256, saved_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (inventory_snapshot_id, snapshot["generation"], snapshot["sequence"],
                     snapshot["observed_at_unix_ms"], snapshot["item_count"], raw,
                     digest, _utc_now()),
                )
                observation_id = int(cursor.lastrowid)
                connection.execute(
                    "DELETE FROM packet_item_observation WHERE observation_id NOT IN "
                    "(SELECT observation_id FROM packet_item_observation "
                    "ORDER BY observation_id DESC LIMIT ?)",
                    (DEFAULT_SNAPSHOT_RETENTION_COUNT,),
                )
                check()
                return observation_id
        except sqlite3.Error as exc:
            raise UserDataError("保存抓包材料观测失败") from exc
