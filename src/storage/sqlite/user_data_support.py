# 提供按账号存储设置、背包快照和装配方案的 SQLite 数据层。
"""按账号存储设置、背包快照和装配方案的 SQLite 数据层。"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.services.virtual_equipment_service import (
    is_virtual_equipment_assignment,
    make_virtual_equipment_assignment,
)

SCHEMA_VERSION = 10
BASE_SCHEMA_VERSION = 1
DEFAULT_SCHEMA_PATH = Path(__file__).with_name("schema") / "001_user_data.sql"
USER_MIGRATIONS = {
    2: Path(__file__).with_name("schema") / "003_user_data_v2.sql",
    3: Path(__file__).with_name("schema") / "004_user_data_v3.sql",
    4: Path(__file__).with_name("schema") / "005_user_data_v4.sql",
    5: Path(__file__).with_name("schema") / "006_user_data_v5.sql",
    6: Path(__file__).with_name("schema") / "007_user_data_v6.sql",
    7: Path(__file__).with_name("schema") / "008_user_data_v7.sql",
    8: Path(__file__).with_name("schema") / "009_user_data_v8.sql",
    9: Path(__file__).with_name("schema") / "010_user_data_v9.sql",
    10: Path(__file__).with_name("schema") / "011_user_data_v10.sql",
}
SYNC_METHODS = frozenset({"nte_core", "gamepad"})
SNAPSHOT_SOURCES = frozenset({"nte_core", "gamepad", "import"})
DEFAULT_SNAPSHOT_RETENTION_COUNT = 20
ALLOCATION_STRATEGIES = frozenset({"role_priority", "drive_priority", "global_optimal"})
SUIT_REQUIREMENT_MODES = frozenset({"none", "two_piece", "four_piece"})


class UserDataError(RuntimeError):
    """用户数据库无效或版本不兼容。"""


class UserDataValidationError(UserDataError, ValueError):
    """传入的 nte-core 或应用数据格式不正确。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decoded(value: str | None, default: Any) -> Any:
    return json.loads(value) if value is not None else default


def _mark_duplicate_modules(items: Sequence[dict[str, Any]]) -> None:
    """标记游戏筛选器无法区分的重复驱动。

    自动装配只能按形状、品质和副词条名称筛选，不能按实际词条数值定位。
    因此同一完整快照中这些字段相同的驱动属于同一重复组。该标记是快照
    派生数据，不修改 nte-core 传入的任何官方字段。
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if item.get("kind") != "module":
            continue
        signature = _json({
            "geometry": str(item.get("geometry") or ""),
            "quality": str(item.get("quality") or "").casefold(),
            "sub_property_ids": sorted(
                str(stat.get("property_id") or "")
                for stat in item.get("sub_stats") or []
                if isinstance(stat, Mapping) and stat.get("property_id")
            ),
        })
        groups.setdefault(signature, []).append(item)

    group_number = 1
    for signature in sorted(groups):
        group = groups[signature]
        if len(group) < 2:
            continue
        group.sort(key=lambda item: (int(item["uid"]["slot"]), int(item["uid"]["serial"])))
        group_id = f"drive_dup_{group_number:03d}"
        group_number += 1
        for index, item in enumerate(group, start=1):
            item["is_duplicate_drive"] = True
            item["duplicate_group_id"] = group_id
            item["duplicate_index"] = index
            item["duplicate_count"] = len(group)
            item["duplicate_signature"] = signature


def _plain_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise UserDataValidationError(f"{label} 必须是对象")
    return dict(value)


def _integer(value: Any, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise UserDataValidationError(f"{label} 必须是整数")
    if minimum is not None and value < minimum:
        raise UserDataValidationError(f"{label} 不能小于 {minimum}")
    return value



