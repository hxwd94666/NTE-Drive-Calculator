# 定义倒带所选槽位的冻结身份与驱动完整性投影。
from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from typing import Mapping

from src.domain.equipment.loadout_plan_scores import assignment_score_key
from src.domain.equipment.virtual_equipment import (
    normalized_equipment_assignment,
    is_virtual_equipment_assignment,
)



@dataclass(frozen=True, slots=True)
class RewindSlotReference:
    character_id: int
    slot_id: int
    plan_id: int | None

    def __post_init__(self):
        values = (self.character_id, self.slot_id) + (() if self.plan_id is None else (self.plan_id,))
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("槽位选择身份必须为正整数")


@dataclass(frozen=True, slots=True)
class RewindSavedDrive:
    shape_id: str
    area: int
    score: float


@dataclass(frozen=True, slots=True)
class RewindSlotSummary:
    reference: RewindSlotReference
    slot_name: str
    slot_key: str
    sort_order: int
    source_snapshot_id: int | None
    score: float | None
    state: str
    reason: str
    drives: tuple[RewindSavedDrive, ...] = ()


def finite_score(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) and number >= 0 else None


def positive_id(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    if isinstance(value, str) and not value.isdecimal():
        return None
    number = int(value)
    return number if number > 0 else None


def supported_saved_plan(plan):
    payload = plan.get("payload") or {}
    schema = payload.get("schema")
    return schema in {"allocation-official-snapshot-v1", "saved-state-official-loadout-v1"} or (
        schema == "game-observed-loadout-v1" and payload.get("source") == "game_inventory"
    )


def shape_key(value):
    return str(value or "").removeprefix("EquipmentGeometry_").casefold()



def default_slot(rows, explicit_slot_id) -> RewindSlotReference | None:
    if explicit_slot_id is not None:
        chosen = next((row for row in rows if row.reference.slot_id == explicit_slot_id), None)
        return chosen.reference if chosen else (
            RewindSlotReference(rows[0].reference.character_id, explicit_slot_id, None) if rows else None
        )
    if len(rows) == 1:
        return rows[0].reference
    eligible = [row for row in rows if row.state == "ready" and row.score is not None]
    if not eligible:
        return None
    return min(eligible, key=lambda row: (-row.score, row.slot_key != "primary",
                                         row.sort_order, row.reference.slot_id)).reference


def read_slot_preferences(preferences, *, strategy="balanced") -> dict[int, int | None]:
    version = preferences.get("slot_selection_version")
    if type(version) is int and version == 2:
        maps = preferences.get("selected_slots_by_strategy")
        raw = maps.get(strategy) if isinstance(maps, Mapping) else None
    else:
        # Seed each strategy separately from the legacy shared choices.
        raw = preferences.get("selected_slots")
    if not isinstance(raw, Mapping):
        return {}
    return {identifier: value if type(version) is int and version in {1, 2} and type(value) is int and value > 0 else None
            for key, value in raw.items() if (identifier := positive_id(key)) is not None}


def references_for_roles(roles, saved_ids):
    result = {}
    for role in roles:
        identifier = role.character_id
        if identifier in saved_ids:
            slot_id = saved_ids[identifier]
            result[identifier] = (default_slot(role.slots, slot_id) if slot_id is not None else None)
            if slot_id is not None and result[identifier] is None:
                result[identifier] = RewindSlotReference(identifier, slot_id, None)
        else:
            result[identifier] = default_slot(role.slots, None)
    return result


def inspect_slot(slot, plan, inventory, shape_map) -> RewindSlotSummary:
    """Validate saved anchors, not the role's current default blueprint.

    All supported official/custom boards have twenty playable cells in a 5x5
    canvas (blueprint_service.official_board/custom_board). Historical anchors
    with twenty distinct legal cells prove full coverage without looking up a
    potentially changed template. An optional frozen mask also verifies holes.
    """
    result = RewindSlotSummary(
        RewindSlotReference(int(slot["character_id"]), int(slot["slot_id"]), positive_id(plan.get("plan_id"))),
        str(slot.get("slot_name") or slot["slot_id"]), str(slot.get("slot_key") or ""),
        int(slot.get("sort_order") or 0), plan.get("source_snapshot_id"),
        finite_score(plan.get("score")), "empty", "尚无保存方案，请先保存或选择其他槽位。",
    )

    def failure(state, reason):
        return replace(result, state=state, reason=reason)

    if not plan:
        return result
    if result.reference.plan_id is None or positive_id(plan.get("character_id")) != result.reference.character_id:
        return failure("layout_invalid", "方案身份或角色归属无效，请重新计算并保存。")
    if not supported_saved_plan(plan):
        return failure("unsupported", "尚无支持的保存方案，请重新计算并保存。")
    assignments = tuple(normalized_equipment_assignment(row) for row in plan.get("assignments") or ())
    modules = tuple(row for row in assignments if row.get("kind") == "module")
    if not modules or any(is_virtual_equipment_assignment(row) for row in modules):
        return failure("incomplete", "驱动未装满，请补齐或选择其他槽位。")
    if positive_id(result.source_snapshot_id) is None:
        return failure("source_missing", "来源装备资料缺失，请重新计算并保存。")
    occupied, seen, drives = set(), set(), []
    # 逐件历史分数优先，缺分不是零；不按最新权重重新解释已有保存事实。
    scores = (plan.get("payload") or {}).get("assignment_scores") or {}
    score_missing = False
    for row in modules:
        uid = (positive_id(row.get("uid_slot")), positive_id(row.get("uid_serial")))
        if None in uid or uid in seen:
            return failure("layout_invalid", "驱动身份或布局资料无效，请重新计算并保存。")
        seen.add(uid)
        item = inventory.get(uid)
        if item is None or item.get("kind") != "module":
            return failure("source_missing", "来源装备资料缺失，请重新计算并保存。")
        shape = shape_map.get(shape_key(item.get("geometry")))
        if shape is None or not shape.get("cells"):
            return failure("layout_invalid", "形状或布局资料不足，请重新计算并保存。")
        anchor = (positive_id(row.get("target_row")), positive_id(row.get("target_column")))
        # 保存锚点使用正式方向形状；未知旋转不能按零旋转补成完整布局。
        if None in anchor or row.get("rotation", 0) not in (None, 0):
            return failure("layout_invalid", "驱动位置或旋转资料不足，请重新计算并保存。")
        cells = {(anchor[0] + int(cell["x"]), anchor[1] + int(cell["y"])) for cell in shape["cells"]}
        if (len(cells) != int(shape["cell_count"]) or occupied & cells
                or any(not (1 <= r <= 5 and 1 <= c <= 5) for r, c in cells)):
            return failure("layout_invalid", "驱动布局重叠或越界，请重新计算并保存。")
        occupied.update(cells)
        value = scores.get(assignment_score_key(row))
        if value is None:
            value = row.get("score")
        score = finite_score(value)
        score_missing |= score is None
        area = int(shape["cell_count"])
        if item.get("grid_count") is not None and positive_id(item["grid_count"]) != area:
            return failure("layout_invalid", "驱动面积与正式形状不符，请重新计算并保存。")
        drives.append(RewindSavedDrive(str(shape["shape_id"]), area, score if score is not None else 0))
    if len(occupied) < 20:
        return failure("incomplete", "驱动未装满，请补齐或选择其他槽位。")
    if len(occupied) != 20:
        return failure("layout_invalid", "驱动占格与二十格图纸不符，请重新计算并保存。")
    mask = (plan.get("payload") or {}).get("drive_board_cells")
    if mask is not None and occupied != {(int(r), int(c)) for r, c in mask}:
        return failure("layout_invalid", "驱动占格与保存图纸不符，请重新计算并保存。")
    if score_missing:
        return failure("score_missing", "评分资料不足，请重新计算并保存。")
    return replace(result, state="ready", reason="驱动完整", drives=tuple(drives))
