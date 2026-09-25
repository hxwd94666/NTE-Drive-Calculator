# 校验客户端全部已观测物品集合，保留来源未知且不生成装备投影。
from __future__ import annotations

import json

ALL_ITEMS_CAPABILITY = "inventory.all_items.v1"
ALL_ITEMS_SCOPE = "all_observed_InventoryContainerMap_containers"
MAX_RAW_BYTES = 64 * 1024 * 1024


def validate_all_item_snapshot(snapshot, *, with_records=True):
    if not isinstance(snapshot, dict) or snapshot.get("domain") != "inventory":
        raise ValueError("完整物品快照的数据域无效")
    for key in ("providerId", "domainKey", "snapshotId"):
        value = snapshot.get(key)
        if not isinstance(value, str) or not value or len(value) > 1024:
            raise ValueError("完整物品快照缺少来源身份")
    for key in ("generation", "revision", "observedUnixUs", "observedMonotonicMs"):
        value = snapshot.get(key)
        if (not isinstance(value, str) or not value.isascii() or not value.isdecimal()
                or len(value) > 20 or int(value) > 18446744073709551615):
            raise ValueError("完整物品快照的修订或时间无效")
    for key, expected in (("ready", True), ("enabled", True), ("dirty", False),
                          ("enumerationComplete", True), ("failed", False),
                          ("truncated", False), ("collectionComplete", True)):
        if snapshot.get(key) is not expected:
            raise ValueError("完整物品快照尚未完成")
    if snapshot.get("collectionScope") != ALL_ITEMS_SCOPE:
        raise ValueError("完整物品快照的采集范围不符")
    count = snapshot.get("recordCount")
    if (type(count) is not int or not 0 <= count <= 32768
            or type(snapshot.get("sourceRecordCount")) is not int
            or snapshot["sourceRecordCount"] != count):
        raise ValueError("完整物品快照的条目数量不一致")
    # Client enumeration never proves server account coverage.
    if (snapshot.get("complete") is not False or snapshot.get("sourceCoverage") != "unknown"
            or snapshot.get("changeCoverage") != "unknown"
            or not isinstance(snapshot.get("missing"), list)
            or any(not isinstance(value, str) for value in snapshot["missing"])):
        raise ValueError("完整物品快照缺少来源覆盖边界")
    if with_records:
        rows = snapshot.get("records")
        if not isinstance(rows, list) or len(rows) != count or any(not isinstance(row, dict) for row in rows):
            raise ValueError("完整物品快照的分页记录不齐全")


def encode_all_item_snapshot(snapshot):
    validate_all_item_snapshot(snapshot)
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_RAW_BYTES:
        raise ValueError("完整物品快照超过存储上限")
    return encoded
