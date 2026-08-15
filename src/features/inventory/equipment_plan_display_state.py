# 将固定快照中的已保存方案和装备投影为配装页展示模型。
"""Pure saved-plan display projections shared by inventory views."""

from __future__ import annotations

from src.domain.loadout_plan_scores import exact_assignment_score_total
from src.features.inventory.equipment_plan_projection import (
    display_shape_id as _display_shape_id,
    official_stat_values as _official_stat_values,
)
from src.features.inventory.saved_plan_badge import display_strategy_mode
from src.features.inventory.warehouse import warehouse_item_icon_path
from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_ADDED_UIDS,
    DIFF_REMOVED,
    EQUIP_IS_CHANGED,
    EQUIP_IS_NEW,
    EQUIP_MAIN_STATS,
    EQUIP_QUALITY,
    EQUIP_SET_NAME,
    EQUIP_SHAPE_ID,
    EQUIP_SUB_STATS,
    EQUIP_TYPE,
    EQUIP_UID,
    ROLE_BLUEPRINT_LAYOUT,
    ROLE_EQUIPPED_DRIVES,
    ROLE_EQUIPPED_TAPE,
    ROLE_LAST_DIFF,
    ROLE_TOTAL_GRADE,
    ROLE_TOTAL_SCORE,
)
from src.services.tape_main_value import full_level_tape_main_value
from src.services.virtual_equipment_service import (
    is_virtual_equipment_assignment,
    normalized_equipment_assignment,
    virtual_equipment_inventory_item,
)
from src.utils.logger import logger

def _inventory_uid_key(uid):
    """Return the snapshot (serial, slot) key from an official display UID."""

    parts = str(uid or "").rsplit("-", 2)
    if len(parts) != 3:
        return None
    try:
        key = int(parts[-1]), int(parts[-2])
    except ValueError:
        return None
    return key if min(key) >= 1 else None


def _sqlite_plan_display_state(
    plan,
    user_dao,
    static_dao,
    *,
    inventory_by_snapshot=None,
    shape_cells=None,
    suit_names=None,
    attribute_ids=None,
):
    """将活动 SQLite 方案转换为配装页展示模型；不读取旧 JSON。"""
    snapshot_id = int(plan["source_snapshot_id"])
    if inventory_by_snapshot is None:
        items = {(row["uid_serial"], row["uid_slot"]): row for row in user_dao.list_inventory_items(snapshot_id)}
    else:
        items = inventory_by_snapshot.get(snapshot_id, {})
    if shape_cells is None:
        shape_cells = {shape["shape_id"]: shape.get("cells") or [] for shape in static_dao.list_shapes()}
    if suit_names is None:
        suit_names = {
            str(suit["suit_id"]): str(suit.get("name_zh") or suit["suit_id"]) for suit in static_dao.list_suits()
        }
    if attribute_ids is None:
        attribute_ids = {str(attribute["attribute_id"]) for attribute in static_dao.list_equipment_attributes()}
    payload = plan.get("payload") or {}
    last_diff = dict(payload.get("last_diff") or {})
    requested_diff_uids = {
        str(
            (entry.get(EQUIP_UID) if isinstance(entry, dict) else entry)
            or ""
        )
        for diff_key in (DIFF_REMOVED, DIFF_ADDED)
        for entry in (last_diff.get(diff_key, ()) or ())
    }
    requested_diff_uids.discard("")
    diff_item_index = {}
    if requested_diff_uids:
        for row in items.values():
            if bool(row.get("virtual")):
                continue
            kind = "module" if row.get("kind") == "module" else "core"
            uid = f"nte-{kind}-{row.get('uid_slot')}-{row.get('uid_serial')}"
            if uid in requested_diff_uids:
                diff_item_index[uid] = _sqlite_inventory_item_display(
                    row,
                    suit_names,
                )
        # Virtual placeholders live only inside the saved plan, not in the
        # immutable inventory snapshot.  Project them into the same diff index
        # so a displaced core remains a tape swap and a displaced module keeps
        # its original shape/area instead of appearing as an unrelated unknown
        # addition.
        for assignment in plan.get("assignments") or ():
            raw = normalized_equipment_assignment(assignment)
            if not is_virtual_equipment_assignment(raw):
                continue
            virtual_item = virtual_equipment_inventory_item(raw)
            kind = "module" if virtual_item.get("kind") == "module" else "core"
            uid = (
                f"nte-{kind}-{virtual_item.get('uid_slot')}-"
                f"{virtual_item.get('uid_serial')}"
            )
            if uid in requested_diff_uids:
                diff_item_index[uid] = _sqlite_inventory_item_display(
                    virtual_item,
                    suit_names,
                )
    for diff_key in (DIFF_REMOVED, DIFF_ADDED):
        hydrated = []
        for diff_item in last_diff.get(diff_key, ()) or ():
            minimal = dict(diff_item) if isinstance(diff_item, dict) else {EQUIP_UID: str(diff_item)}
            source = diff_item_index.get(str(minimal.get(EQUIP_UID) or ""), {})
            hydrated.append({**source, **minimal})
        if hydrated:
            last_diff[diff_key] = hydrated
    added_uids = {str(uid) for uid in (last_diff.get(DIFF_ADDED_UIDS) or ()) if uid}
    changed_uids = {str(uid) for uid in (payload.get("changed_uids") or ()) if uid}
    board = [["0" for _ in range(5)] for _ in range(5)]
    drives = []
    tape = None
    for assignment in plan["assignments"]:
        raw = normalized_equipment_assignment(assignment)
        item = (
            virtual_equipment_inventory_item(raw)
            if is_virtual_equipment_assignment(raw)
            else items.get((assignment["uid_serial"], assignment["uid_slot"]))
        )
        if item is None:
            message = (
                f"方案 #{plan.get('plan_id')} 的装备 UID "
                f"({assignment.get('uid_slot')}, {assignment.get('uid_serial')}) "
                f"不在来源快照 {snapshot_id} 中"
            )
            logger.error("已保存方案兼容性错误：{}", message)
            raise RuntimeError(message)
        unknown_properties = [
            str(stat.get("property_id") or "")
            for stat in (*item.get("main_stats", ()), *item.get("sub_stats", ()))
            if str(stat.get("property_id") or "") not in attribute_ids
        ]
        if unknown_properties:
            message = (
                f"方案 #{plan.get('plan_id')} 的来源快照包含当前静态数据未定义的属性 ID："
                f"{', '.join(sorted(set(unknown_properties)))}"
            )
            logger.error("已保存方案兼容性错误：{}", message)
            raise RuntimeError(message)
        uid_prefix = "module" if item["kind"] == "module" else "core"
        uid = f"nte-{uid_prefix}-{item['uid_slot']}-{item['uid_serial']}"
        item_icon_path = warehouse_item_icon_path(item)
        if item["kind"] == "core":
            suit_id = str(item.get("suit_id") or "")
            if suit_id not in suit_names:
                message = f"方案 #{plan.get('plan_id')} 的核心套装 {suit_id or '<empty>'} 不在当前静态数据中"
                logger.error("已保存方案兼容性错误：{}", message)
                raise RuntimeError(message)
            main_stats = _official_stat_values(item.get("main_stats"))
            main_stat, snapshot_main_value = next(iter(main_stats.items()), ("未知主词条", None))
            saved_main_values = payload.get("tape_main_values") or {}
            main_value = saved_main_values.get(uid)
            if main_value is None:
                main_value = full_level_tape_main_value(main_stat, item.get("quality"))
            if main_value is None:
                main_value = snapshot_main_value
            tape = {
                EQUIP_UID: uid,
                EQUIP_SET_NAME: suit_names.get(str(item.get("suit_id") or ""), str(item.get("suit_id") or "未知套装")),
                EQUIP_MAIN_STATS: main_stat,
                # Keep the exact snapshot value.  The card and both attribute
                # summaries must not replace an imported core stat with a
                # quality-based fallback estimate.
                "main_value": main_value,
                "_role_main_stats": main_stats,
                EQUIP_SUB_STATS: _official_stat_values(item.get("sub_stats")),
                EQUIP_QUALITY: {"orange": "Gold", "purple": "Purple", "blue": "Blue"}.get(
                    str(item.get("quality")).casefold(), "Gold"
                ),
                "discarded": bool(item.get("discarded")),
                "item_icon_path": item_icon_path,
                "virtual": bool(item.get("virtual")),
                EQUIP_IS_CHANGED: uid in changed_uids,
                EQUIP_IS_NEW: uid in added_uids and uid not in changed_uids,
            }
            continue
        geometry = item.get("geometry")
        shape_id = _display_shape_id(geometry)
        official_shape = "EquipmentGeometry_" + str(geometry or "").removeprefix("EquipmentGeometry_")
        if official_shape not in shape_cells:
            message = f"方案 #{plan.get('plan_id')} 的驱动形状 {geometry or '<empty>'} 不在当前静态数据中"
            logger.error("已保存方案兼容性错误：{}", message)
            raise RuntimeError(message)
        drives.append(
            {
                EQUIP_UID: uid,
                EQUIP_SHAPE_ID: shape_id,
                EQUIP_SUB_STATS: _official_stat_values(item.get("sub_stats")),
                EQUIP_QUALITY: {"orange": "Gold", "purple": "Purple", "blue": "Blue"}.get(
                    str(item.get("quality")).casefold(), "Gold"
                ),
                "discarded": bool(item.get("discarded")),
                # Snapshot ingestion derives duplicate state for modules.  Keep it
                # on the saved-plan view model so the card can show it alongside
                # discard/new/change state.
                "is_duplicate_drive": bool(item.get("is_duplicate_drive")),
                "duplicate_group_id": item.get("duplicate_group_id"),
                "duplicate_index": item.get("duplicate_index"),
                "duplicate_count": item.get("duplicate_count"),
                "item_icon_path": item_icon_path,
                "virtual": bool(item.get("virtual")),
                EQUIP_IS_CHANGED: uid in changed_uids,
                EQUIP_IS_NEW: uid in added_uids and uid not in changed_uids,
            }
        )
        row, column = assignment.get("target_row"), assignment.get("target_column")
        for cell in shape_cells.get(official_shape, []):
            target_row = int(row) + int(cell["x"]) - 1
            target_column = int(column) + int(cell["y"]) - 1
            if 0 <= target_row < 5 and 0 <= target_column < 5:
                board[target_row][target_column] = shape_id
    return {
        ROLE_BLUEPRINT_LAYOUT: board,
        ROLE_EQUIPPED_TAPE: tape,
        ROLE_EQUIPPED_DRIVES: drives,
        ROLE_TOTAL_SCORE: float(plan.get("score") or 0.0),
        ROLE_TOTAL_GRADE: "",
        # A plan imported from the game is a mutually exclusive origin marker,
        # not a result of the strategy that happened to be active earlier.
        "strategy_mode": display_strategy_mode(payload),
        "_sqlite_plan_id": plan["plan_id"],
        "_sqlite_source_snapshot_id": snapshot_id,
        "_sqlite_assignment_scores_complete": exact_assignment_score_total(
            plan["assignments"],
            payload.get("assignment_scores") or {},
        ) is not None,
        "_allocation_locked": bool(plan.get("allocation_locked")),
        ROLE_LAST_DIFF: last_diff,
    }


def _sqlite_inventory_item_display(row, suit_names):
    """Project one official snapshot item for the saved-plan replacement dialog."""
    kind = str(row.get("kind") or "")
    quality = {"orange": "Gold", "purple": "Purple", "blue": "Blue"}.get(
        str(row.get("quality") or "").casefold(), "Gold"
    )
    uid = f"nte-{'module' if kind == 'module' else 'core'}-{row.get('uid_slot')}-{row.get('uid_serial')}"
    if kind == "core":
        main_stats = _official_stat_values(row.get("main_stats"))
        main_stat, snapshot_main_value = next(iter(main_stats.items()), ("未知主词条", None))
        main_value = full_level_tape_main_value(main_stat, row.get("quality"))
        if main_value is None:
            main_value = snapshot_main_value
        return {
            EQUIP_UID: uid,
            EQUIP_TYPE: "tape",
            EQUIP_SET_NAME: (
                "空空幕"
                if row.get("virtual")
                else suit_names.get(
                    str(row.get("suit_id") or ""),
                    str(row.get("suit_id") or "未知套装"),
                )
            ),
            EQUIP_MAIN_STATS: main_stat,
            "main_value": main_value,
            EQUIP_SUB_STATS: _official_stat_values(row.get("sub_stats")),
            EQUIP_QUALITY: quality,
            "_role_main_stats": main_stats,
            "_item_id": str(row.get("item_id") or ""),
            "_uid_serial": int(row.get("uid_serial") or 0),
            "_uid_slot": int(row.get("uid_slot") or 0),
            "item_icon_path": warehouse_item_icon_path(row),
            "virtual": bool(row.get("virtual")),
        }
    return {
        EQUIP_UID: uid,
        EQUIP_TYPE: "drive",
        EQUIP_SHAPE_ID: _display_shape_id(row.get("geometry")),
        EQUIP_SUB_STATS: _official_stat_values(row.get("sub_stats")),
        EQUIP_QUALITY: quality,
        "_item_id": str(row.get("item_id") or ""),
        "is_duplicate_drive": bool(row.get("is_duplicate_drive")),
        "duplicate_group_id": row.get("duplicate_group_id"),
        "duplicate_index": row.get("duplicate_index"),
        "duplicate_count": row.get("duplicate_count"),
        "_uid_serial": int(row.get("uid_serial") or 0),
        "_uid_slot": int(row.get("uid_slot") or 0),
        "item_icon_path": warehouse_item_icon_path(row),
        "virtual": bool(row.get("virtual")),
    }
