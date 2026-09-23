# 解析弧盘经验材料的正式物品规格。
"""Narrow parsing helpers for normalized fork progression materials."""

from __future__ import annotations

from typing import Any, Callable

from tools.game_data.static_database_build_support import (
    StaticDatabaseError,
    enum_tail,
)


def fork_exp_material_spec(
    item_id: str,
    row: Any,
    *,
    parse_cost_string: Callable[[Any], list[tuple[str, int]]],
) -> tuple[int, list[tuple[str, int]]] | None:
    suffix = str(item_id).removeprefix("WeaponUpMaterial_lv")
    if not suffix.isdigit():
        return None
    if not isinstance(row, dict) or "DevelopMaterial" not in (row.get("ItemTags") or ()):
        raise StaticDatabaseError(f"弧盘经验材料结构无效：{item_id}")
    item_type = enum_tail(row.get("ItemType"), "ITEM_TYPE_")
    if item_type != "FORK_MATERIAL":
        raise StaticDatabaseError(f"弧盘经验材料类型无效：{item_id}")
    element = row.get("ElementData")
    if not isinstance(element, dict):
        raise StaticDatabaseError(f"弧盘经验材料缺少 ElementData：{item_id}")
    experience = element.get("EXP")
    if isinstance(experience, bool) or not isinstance(experience, int) or experience <= 0:
        raise StaticDatabaseError(f"弧盘经验材料 EXP 无效：{item_id}")
    costs = parse_cost_string(element.get("CostGold"))
    if not costs:
        raise StaticDatabaseError(f"弧盘经验材料缺少正式方斯消耗：{item_id}")
    return experience, costs


__all__ = ["fork_exp_material_spec"]
