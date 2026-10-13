# 标准化旧库存、扫描结果和已保存配装中的装备字段。
"""Equipment schema normalization for inventory and extension algorithms."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def calculate_drive_main_stats(area: int, quality: str) -> dict[str, float]:
    multiplier = {"Gold": 1.0, "Purple": 0.8, "Blue": 0.6}.get(str(quality), 1.0)
    return {
        "攻击力": round(21.0 * int(area or 1) * multiplier, 2),
        "生命值": round(280.0 * int(area or 1) * multiplier, 2),
    }


def _first_key_or_unknown(value: Any, unknown: str) -> str:
    if isinstance(value, dict):
        return str(next(iter(value.keys()), unknown))
    text = str(value or "").strip()
    return text or unknown


def normalize_equipment_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(item)
    item_type = normalized.get("item_type")
    normalized.setdefault("sub_stats", {})
    normalized.setdefault("role_scores", {})
    normalized.setdefault("max_score", 0.0)
    normalized.setdefault("is_mvp", False)
    normalized.setdefault("pick_order", 0)

    if item_type == "drive":
        area = int(normalized.get("area") or 1)
        normalized["area"] = area
        normalized.setdefault("quality", "Gold")
        normalized.setdefault("shape_id", f"DRIVE_{area}")
        normalized.setdefault("set_name", "未知套装")
        main_stats = normalized.get("main_stats")
        if not isinstance(main_stats, dict) or len(main_stats) != 2:
            normalized["main_stats"] = calculate_drive_main_stats(area, normalized.get("quality", "Gold"))
        return normalized

    if item_type == "tape":
        normalized["area"] = 15
        normalized["shape_id"] = "TAPE_15"
        normalized.setdefault("quality", "Gold")
        normalized.setdefault("set_name", "未知套装")
        normalized["main_stats"] = _first_key_or_unknown(normalized.get("main_stats"), "未知主词条")
        return normalized

    return normalized


def normalize_inventory(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_equipment_item(item) for item in items]
