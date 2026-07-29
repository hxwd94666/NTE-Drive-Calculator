# 从只读静态数据库选择仓库占位展示所需的代表装备 ID。
"""Resolve representative official equipment IDs for warehouse visuals."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


_GEOMETRY_LABELS = {
    "hen2": "H_2", "hen3": "H_3", "hen4": "H_4",
    "shu2": "V_2", "shu3": "V_3", "shu4": "V_4",
    "z3": "Trap_4_H", "z4": "Trap_4_V",
    "zhijiao1": "L_3_BL", "zhijiao2": "L_3_TL",
    "zhijiao3": "L_3_TR", "zhijiao4": "L_3_BR",
}


def _shape_label(value: Any) -> str:
    key = str(value or "").removeprefix("EquipmentGeometry_").casefold()
    return _GEOMETRY_LABELS.get(key, str(value or ""))


def _quality_key(value: Any) -> str:
    text = str(value or "").casefold()
    if any(token in text for token in ("gold", "golden", "orange", "金", "橙")):
        return "gold"
    if "purple" in text or "紫" in text:
        return "purple"
    if "blue" in text or "蓝" in text:
        return "blue"
    if "green" in text or "绿" in text:
        return "green"
    return text or "unknown"


@lru_cache(maxsize=48)
def representative_module_item_id(shape: str, quality: str) -> str:
    try:
        with StaticGameDataDao() as static_dao:
            rows = static_dao.list_equipment_items("module")
    except Exception:
        return ""
    for row in rows:
        if (
            _shape_label(row.get("geometry_id")) == shape
            and _quality_key(row.get("quality")) == quality
        ):
            return str(row.get("item_id") or "")
    return ""


@lru_cache(maxsize=64)
def representative_core_item_id(suit_id: str, quality: str) -> str:
    try:
        with StaticGameDataDao() as static_dao:
            rows = static_dao.list_equipment_items("core")
    except Exception:
        return ""
    matches = [
        row
        for row in rows
        if str(row.get("suit_id") or "") == suit_id
        and _quality_key(row.get("quality")) == quality
    ]
    for row in matches:
        if not bool(row.get("is_guide_item")):
            return str(row.get("item_id") or "")
    return str(matches[0].get("item_id") or "") if matches else ""
