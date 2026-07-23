# 为跨角色借装生成仅存在于装配方案中的金色占位装备。
"""Virtual equipment helpers shared by temporary and persisted loadouts."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from src.domain.equipment_normalizer import calculate_drive_main_stats


VIRTUAL_UID_SLOT = 0


def is_virtual_equipment_assignment(assignment: Mapping[str, Any]) -> bool:
    """Return whether a loadout assignment is a non-inventory placeholder."""

    return bool(assignment.get("virtual")) or (
        int(assignment.get("uid_slot") or 0) == VIRTUAL_UID_SLOT
        and isinstance(assignment.get("virtual_equipment"), Mapping)
    )


def grid_count_from_geometry(geometry: Any) -> int:
    numbers = re.findall(r"\d+", str(geometry or ""))
    return int(numbers[-1]) if numbers else 0


def virtual_equipment_uid(
    *,
    character_id: int,
    displaced_uid: tuple[int, int],
    ordinal: int,
    kind: str,
) -> tuple[int, int]:
    """Build a deterministic SQLite-safe UID outside the native UID space."""

    token = (
        f"{int(character_id)}:{str(kind)}:{int(displaced_uid[0])}:"
        f"{int(displaced_uid[1])}:{int(ordinal)}"
    ).encode("utf-8")
    serial = int.from_bytes(
        hashlib.blake2b(token, digest_size=8).digest(), "big"
    ) & ((1 << 63) - 1)
    return VIRTUAL_UID_SLOT, serial or 1


def virtual_equipment_item_id(kind: str, geometry: Any = None) -> str:
    if str(kind) == "module":
        suffix = str(geometry or "unknown").removeprefix("EquipmentGeometry_")
        return f"virtual-module-{suffix}"
    return "virtual-core"


def make_virtual_equipment_assignment(
    source: Mapping[str, Any],
    *,
    inventory_item: Mapping[str, Any] | None,
    character_id: int,
    ordinal: int,
) -> dict[str, Any]:
    """Replace one real assignment with an empty same-slot gold placeholder."""

    item = inventory_item or {}
    kind = str(source.get("kind") or item.get("kind") or "")
    if kind not in {"module", "core"}:
        raise ValueError("虚拟占位装备类型必须是 module 或 core")
    displaced_uid = (
        int(source.get("uid_slot") or 0),
        int(source.get("uid_serial") or 0),
    )
    geometry = (
        str(source.get("geometry") or item.get("geometry") or "") or None
    )
    grid_count = int(
        source.get("grid_count")
        or item.get("grid_count")
        or grid_count_from_geometry(geometry)
        or 0
    )
    uid_slot, uid_serial = virtual_equipment_uid(
        character_id=character_id,
        displaced_uid=displaced_uid,
        ordinal=ordinal,
        kind=kind,
    )
    result = dict(source)
    result.update(
        {
            "uid_slot": uid_slot,
            "uid_serial": uid_serial,
            "kind": kind,
            "geometry": geometry if kind == "module" else None,
            "grid_count": grid_count if kind == "module" else None,
            "virtual": True,
            "virtual_equipment": {
                "item_id": str(
                    source.get("item_id")
                    or item.get("item_id")
                    or virtual_equipment_item_id(kind, geometry)
                ),
                "kind": kind,
                "suit_id": source.get("suit_id") or item.get("suit_id"),
                "names": dict(item.get("names") or {}),
                "suit_names": dict(item.get("suit_names") or {}),
                "geometry": geometry if kind == "module" else None,
                "grid_count": grid_count if kind == "module" else None,
                "quality": "orange",
            },
        }
    )
    return result


def virtual_equipment_inventory_item(
    assignment: Mapping[str, Any],
) -> dict[str, Any]:
    """Project a persisted placeholder into the normal equipment-card shape."""

    metadata = assignment.get("virtual_equipment")
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    kind = str(metadata.get("kind") or assignment.get("kind") or "")
    geometry = metadata.get("geometry") or assignment.get("geometry")
    grid_count = int(
        metadata.get("grid_count")
        or assignment.get("grid_count")
        or grid_count_from_geometry(geometry)
        or 0
    )
    main_stats: list[dict[str, Any]] = []
    if kind == "module" and grid_count > 0:
        intrinsic = calculate_drive_main_stats(grid_count, "Gold")
        main_stats = [
            {
                "property_id": "AtkAdd",
                "value": float(intrinsic["攻击力"]),
                "percent": False,
                "names": {"zh_cn": "攻击力"},
            },
            {
                "property_id": "HPMaxAdd",
                "value": float(intrinsic["生命值"]),
                "percent": False,
                "names": {"zh_cn": "生命值"},
            },
        ]
    display_name = "空驱动" if kind == "module" else "空空幕"
    names = dict(metadata.get("names") or {})
    suit_names = dict(metadata.get("suit_names") or {})
    if not names:
        names = {"zh_cn": display_name}
    if not suit_names:
        suit_names = {"zh_cn": display_name}
    return {
        "uid": {
            "slot": int(assignment.get("uid_slot") or 0),
            "serial": int(assignment.get("uid_serial") or 0),
        },
        "uid_slot": int(assignment.get("uid_slot") or 0),
        "uid_serial": int(assignment.get("uid_serial") or 0),
        "kind": kind,
        "item_id": str(
            metadata.get("item_id")
            or virtual_equipment_item_id(kind, geometry)
        ),
        "suit_id": metadata.get("suit_id"),
        "geometry": geometry if kind == "module" else None,
        "grid_count": grid_count if kind == "module" else None,
        "quality": "orange",
        "level": 0,
        "max_level": 0,
        "names": names,
        "suit_names": suit_names,
        "main_stats": main_stats,
        "sub_stats": [],
        "virtual": True,
        "equipped": False,
        "locked": False,
        "discarded": False,
    }
