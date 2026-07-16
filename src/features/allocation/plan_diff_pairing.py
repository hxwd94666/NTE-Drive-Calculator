# 配装变动里旧/新驱动盘的配对规则。
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from src.optimizer.contracts import EQUIP_AREA, EQUIP_SCORE_AREA, EQUIP_SHAPE_ID

_DRIVE_SHAPE_FAMILY = {
    "H_2": "I_2",
    "V_2": "I_2",
    "H_3": "I_3",
    "V_3": "I_3",
    "H_4": "I_4",
    "V_4": "I_4",
    "L_3_TL": "L_3",
    "L_3_TR": "L_3",
    "L_3_BL": "L_3",
    "L_3_BR": "L_3",
    "Trap_4_H": "Trap_4",
    "Trap_4_V": "Trap_4",
}


def drive_shape_family(shape_id: str) -> str:
    return _DRIVE_SHAPE_FAMILY.get(shape_id, shape_id)


def drive_item_area(item: dict[str, Any] | Any, shape_areas: dict[str, Any] | None = None) -> int:
    if isinstance(item, dict):
        area = item.get(EQUIP_AREA) or item.get(EQUIP_SCORE_AREA)
        shape_id = str(item.get(EQUIP_SHAPE_ID, "") or "")
    else:
        area = getattr(item, EQUIP_AREA, None) or getattr(item, EQUIP_SCORE_AREA, None)
        shape_id = str(getattr(item, EQUIP_SHAPE_ID, None) or getattr(item, "shape_id", "") or "")
    if area is not None:
        try:
            return int(area)
        except (TypeError, ValueError):
            pass
    areas = shape_areas or {}
    if shape_id in areas:
        try:
            return int(areas[shape_id])
        except (TypeError, ValueError):
            pass
    match = re.search(r"_(\d+)", shape_id)
    return int(match.group(1)) if match else 0


def pair_diff_items_by_key(
    old_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
    key_fn: Callable[[dict[str, Any]], Any],
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    old_buckets: dict[Any, list[dict[str, Any]]] = {}
    for item in old_items:
        old_buckets.setdefault(key_fn(item), []).append(item)
    new_buckets: dict[Any, list[dict[str, Any]]] = {}
    for item in new_items:
        new_buckets.setdefault(key_fn(item), []).append(item)

    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    unmatched_old: list[dict[str, Any]] = []
    unmatched_new: list[dict[str, Any]] = []
    for key in sorted(set(old_buckets) | set(new_buckets)):
        old_group = old_buckets.get(key, [])
        new_group = new_buckets.get(key, [])
        count = min(len(old_group), len(new_group))
        pairs.extend(zip(old_group[:count], new_group[:count]))
        unmatched_old.extend(old_group[count:])
        unmatched_new.extend(new_group[count:])
    return pairs, unmatched_old, unmatched_new


def pair_drive_diff_items(
    removed_drives: list[dict[str, Any]],
    added_drives: list[dict[str, Any]],
    shape_areas: dict[str, Any] | None = None,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    areas = shape_areas or {}
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    unmatched_old = list(removed_drives)
    unmatched_new = list(added_drives)
    shape_id = lambda item: str(item.get(EQUIP_SHAPE_ID, "") or "")
    for key_fn in (
        shape_id,
        lambda item: drive_shape_family(shape_id(item)),
        lambda item: drive_item_area(item, areas),
    ):
        round_pairs, unmatched_old, unmatched_new = pair_diff_items_by_key(unmatched_old, unmatched_new, key_fn)
        pairs.extend(round_pairs)
    return pairs, unmatched_old, unmatched_new
