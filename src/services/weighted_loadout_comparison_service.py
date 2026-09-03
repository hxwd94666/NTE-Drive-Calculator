# 冻结词条配装结果与各已保存槽位之间的装备差异。
"""Qt-free saved-slot comparison projections for weighted allocation results."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from src.domain.allocation_rating import allocation_grade
from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_ADDED_UIDS,
    DIFF_CHANGED,
    DIFF_REMOVED,
    EQUIP_AREA,
    EQUIP_DISPLAY_NAME,
    EQUIP_GRADE,
    EQUIP_MAIN_STATS,
    EQUIP_QUALITY,
    EQUIP_SCORE,
    EQUIP_SCORE_AREA,
    EQUIP_SET_NAME,
    EQUIP_SHAPE_ID,
    EQUIP_SUB_STATS,
    EQUIP_TYPE,
    EQUIP_UID,
)
from src.services.virtual_equipment_service import (
    grid_count_from_geometry,
    is_virtual_equipment_assignment,
    normalized_equipment_assignment,
    virtual_equipment_inventory_item,
)
from src.services.blueprint_service import OFFICIAL_SHAPE_LABELS


@dataclass(frozen=True, slots=True)
class WeightedLoadoutComparison:
    slot_id: int
    slot_name: str
    slot_key: str
    old_items: tuple[dict[str, Any], ...]
    diff: Mapping[str, Any]


def _uid(kind: str, slot: int, serial: int) -> str:
    prefix = "module" if kind == "module" else "core"
    return f"nte-{prefix}-{int(slot)}-{int(serial)}"


def _quality(value: Any) -> str:
    return {
        "orange": "Gold",
        "gold": "Gold",
        "purple": "Purple",
        "blue": "Blue",
    }.get(str(value or "Gold").casefold(), str(value or "Gold"))


def _shape_key(value: Any) -> str:
    return str(value or "").removeprefix("EquipmentGeometry_").casefold()


def _shape_name(value: Any, shape_names: Mapping[str, str]) -> str:
    geometry = str(value or "")
    normalized_names = {
        _shape_key(shape_id): str(label)
        for shape_id, label in {
            **OFFICIAL_SHAPE_LABELS,
            **dict(shape_names),
        }.items()
    }
    return normalized_names.get(
        _shape_key(geometry),
        geometry.removeprefix("EquipmentGeometry_") or "驱动",
    )


def _stat_map(stats: Any, labels: Mapping[str, str]) -> dict[str, float]:
    return {
        labels.get(str(stat.get("property_id") or ""), str(stat.get("property_id") or "")):
        float(stat.get("value") or 0.0) * (100.0 if stat.get("percent") else 1.0)
        for stat in stats or ()
        if str(stat.get("property_id") or "")
    }


def _old_item_snapshot(
    item: Mapping[str, Any],
    assignment: Mapping[str, Any],
    *,
    score: float,
    labels: Mapping[str, str],
    suit_names: Mapping[str, str],
    shape_names: Mapping[str, str],
) -> dict[str, Any]:
    kind = str(item.get("kind") or assignment.get("kind") or "")
    area = (
        15
        if kind == "core"
        else int(item.get("grid_count") or 0)
        or grid_count_from_geometry(item.get("geometry"))
    )
    quality = _quality(item.get("quality"))
    uid = _uid(kind, assignment["uid_slot"], assignment["uid_serial"])
    common = {
        EQUIP_UID: uid,
        EQUIP_TYPE: "tape" if kind == "core" else "drive",
        EQUIP_QUALITY: quality,
        EQUIP_SCORE: round(float(score), 2),
        EQUIP_SCORE_AREA: area,
        EQUIP_AREA: area,
        EQUIP_GRADE: allocation_grade(float(score), area) if area else "D",
        EQUIP_SUB_STATS: _stat_map(item.get("sub_stats"), labels),
    }
    if kind == "core":
        main_stats = _stat_map(item.get("main_stats"), labels)
        main_name = next(iter(main_stats), "未知主词条")
        set_name = suit_names.get(str(item.get("suit_id") or ""), str(item.get("suit_id") or "空空幕"))
        return {
            **common,
            EQUIP_SET_NAME: set_name,
            EQUIP_MAIN_STATS: main_name,
            EQUIP_DISPLAY_NAME: f"{set_name}-{main_name}",
        }
    shape_name = _shape_name(item.get("geometry"), shape_names)
    return {
        **common,
        EQUIP_SHAPE_ID: shape_name,
        EQUIP_DISPLAY_NAME: shape_name,
    }


def _new_item_snapshots(context: Any, option: Any, labels: Mapping[str, str], suit_names: Mapping[str, str], shape_names: Mapping[str, str]) -> tuple[dict[str, Any], ...]:
    candidates = {candidate.uid: candidate for candidate in context.candidates}
    rows = []
    for assignment in option.assignments:
        candidate = candidates.get(assignment.uid)
        kind = str(assignment.kind)
        area = 15 if kind == "core" else int(assignment.grid_count or getattr(candidate, "grid_count", 0) or 0)
        quality = _quality(getattr(candidate, "quality", "orange"))
        sub_stats = {
            labels.get(str(stat.property_id), str(stat.property_id)):
            float(stat.value) * (100.0 if stat.percent else 1.0)
            for stat in (getattr(candidate, "sub_stats", ()) or ())
        }
        common = {
            EQUIP_UID: _uid(kind, assignment.uid[0], assignment.uid[1]),
            EQUIP_TYPE: "tape" if kind == "core" else "drive",
            EQUIP_QUALITY: quality,
            EQUIP_SCORE: round(float(assignment.score), 2),
            EQUIP_SCORE_AREA: area,
            EQUIP_AREA: area,
            EQUIP_GRADE: allocation_grade(float(assignment.score), area) if area else "D",
            EQUIP_SUB_STATS: sub_stats,
        }
        if kind == "core":
            main = next(iter(getattr(candidate, "main_stats", ()) or ()), None)
            main_name = labels.get(str(getattr(main, "property_id", "") or ""), str(getattr(main, "property_id", "") or "未知主词条"))
            set_id = str(getattr(candidate, "suit_id", None) or assignment.suit_id or "")
            set_name = suit_names.get(set_id, set_id or "空空幕")
            rows.append({**common, EQUIP_SET_NAME: set_name, EQUIP_MAIN_STATS: main_name, EQUIP_DISPLAY_NAME: f"{set_name}-{main_name}"})
        else:
            shape_name = _shape_name(assignment.geometry, shape_names)
            rows.append({**common, EQUIP_SHAPE_ID: shape_name, EQUIP_DISPLAY_NAME: shape_name})
    return tuple(rows)


def _diff(old_items: Sequence[Mapping[str, Any]], new_items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    old = {str(item[EQUIP_UID]): dict(item) for item in old_items}
    new = {str(item[EQUIP_UID]): dict(item) for item in new_items}
    added = set(new) - set(old)
    removed = set(old) - set(new)
    return {
        DIFF_CHANGED: bool(added or removed),
        DIFF_ADDED_UIDS: added,
        DIFF_ADDED: tuple(new[uid] for uid in new if uid in added),
        DIFF_REMOVED: tuple(old[uid] for uid in old if uid in removed),
    }


def freeze_weighted_loadout_comparisons(user_dao: Any, static_dao: Any, context: Any, options: Sequence[Any]) -> dict[int, tuple[WeightedLoadoutComparison, ...]]:
    """Freeze every visible saved-slot baseline with the calculation result."""

    attributes = {str(row["attribute_id"]): str(row.get("display_name_zh") or row["attribute_id"]) for row in static_dao.list_equipment_attributes()}
    suits = {str(row["suit_id"]): str(row.get("name_zh") or row["suit_id"]) for row in static_dao.list_suits()}
    shapes = {
        str(row["shape_id"]): str(row.get("legacy_shape_id") or row.get("legacy_label") or row["shape_id"]).removeprefix("EquipmentGeometry_")
        for row in static_dao.list_shapes()
    }
    result: dict[int, tuple[WeightedLoadoutComparison, ...]] = {}
    for option in options:
        comparisons = []
        for slot in user_dao.list_loadout_slots(int(option.character_id)):
            plan = slot.get("current_plan")
            if not isinstance(plan, Mapping):
                continue
            assignments = tuple(plan.get("assignments") or ())
            real_uids = {
                (int(row["uid_serial"]), int(row["uid_slot"]))
                for row in assignments
                if not is_virtual_equipment_assignment(normalized_equipment_assignment(row))
            }
            inventory = {
                (int(row["uid_serial"]), int(row["uid_slot"])): row
                for row in user_dao.list_inventory_items(int(plan["source_snapshot_id"]), uids=real_uids)
            }
            saved_scores = (plan.get("payload") or {}).get("assignment_scores") or {}
            old_items = []
            for assignment in assignments:
                normalized = normalized_equipment_assignment(assignment)
                item = virtual_equipment_inventory_item(normalized) if is_virtual_equipment_assignment(normalized) else inventory.get((int(assignment["uid_serial"]), int(assignment["uid_slot"])))
                if item is None:
                    continue
                uid = _uid(str(assignment.get("kind") or ""), assignment["uid_slot"], assignment["uid_serial"])
                old_items.append(_old_item_snapshot(item, assignment, score=float(saved_scores.get(uid, 0.0)), labels=attributes, suit_names=suits, shape_names=shapes))
            new_items = _new_item_snapshots(context, option, attributes, suits, shapes)
            comparisons.append(WeightedLoadoutComparison(
                slot_id=int(slot["slot_id"]),
                slot_name=str(slot.get("slot_name") or slot["slot_id"]),
                slot_key=str(slot.get("slot_key") or ""),
                old_items=tuple(old_items),
                diff=_diff(old_items, new_items),
            ))
        result[int(option.character_id)] = tuple(comparisons)
    return result


def refresh_weighted_loadout_comparisons(comparisons: Mapping[int, Sequence[WeightedLoadoutComparison]], context: Any, options: Sequence[Any]) -> dict[int, tuple[WeightedLoadoutComparison, ...]]:
    """Rebuild diffs after an in-preview manual replacement."""

    option_map = {int(option.character_id): option for option in options}
    labels = {str(row.property_id): str(row.scoring_name) for row in getattr(context, "attributes", ())}
    result = {}
    for character_id, rows in comparisons.items():
        option = option_map.get(int(character_id))
        if option is None:
            continue
        new_items = _new_item_snapshots(context, option, labels, {}, {})
        result[int(character_id)] = tuple(replace(row, diff=_diff(row.old_items, new_items)) for row in rows)
    return result
