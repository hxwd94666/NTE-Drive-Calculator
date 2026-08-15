# 在工作线程中加载固定快照配装与游戏配装投影。
"""Qt-free display state loaders for saved and observed loadouts."""

from __future__ import annotations

from typing import Any

from src.features.inventory.equipment_plan_display_state import (
    _inventory_uid_key,
    _sqlite_inventory_item_display,
    _sqlite_plan_display_state,
)
from src.services.virtual_equipment_service import (
    is_virtual_equipment_assignment,
    normalized_equipment_assignment,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import logger

def _load_sqlite_equipment_display_states(
    database_path,
    *,
    static_database_path=None,
):
    """Read display-only saved plans off the UI thread with shared snapshots.

    A multi-role allocation commonly binds every plan to one immutable
    snapshot.  Re-reading that snapshot and static catalogs per card was the
    principal loading bottleneck.
    """
    with UserDataDao(database_path) as user_dao, StaticGameDataDao(static_database_path) as static_dao:
        load_slot_plans = getattr(user_dao, "list_current_loadout_slot_plans", None)
        if callable(load_slot_plans):
            slot_plans = load_slot_plans()
            visible_slots = user_dao.list_visible_loadout_slots()
            plans = {
                f"slot:{row['slot']['slot_id']}": (row["slot"], row["plan"])
                for row in slot_plans
            }
        else:
            # Narrow test and pre-v15 compatibility hosts expose only the
            # historical active-role projection. Production accounts always
            # use the slot-aware branch above.
            legacy_plans = user_dao.list_active_loadout_plans_by_role()
            visible_slots = []
            plans = {
                role_name: (
                    {
                        "slot_id": -int(plan.get("plan_id") or index + 1),
                        "character_id": int(plan.get("character_id") or 0),
                        "slot_key": "primary",
                        "slot_name": role_name,
                    },
                    plan,
                )
                for index, (role_name, plan) in enumerate(legacy_plans.items())
            }
        historical_plans = user_dao.list_loadout_plans()
        snapshot_ids = {
            int(plan["source_snapshot_id"])
            for _slot, plan in plans.values()
            if plan.get("source_snapshot_id") is not None
        }
        assignment_uids_by_snapshot: dict[int, set[tuple[int, int]]] = {
            snapshot_id: set() for snapshot_id in snapshot_ids
        }
        diff_uids_by_snapshot: dict[int, set[tuple[int, int]]] = {
            snapshot_id: set() for snapshot_id in snapshot_ids
        }
        historical_snapshot_by_uid: dict[tuple[int, int], int] = {}
        for historical_plan in historical_plans:
            historical_snapshot_id = historical_plan.get("source_snapshot_id")
            if historical_snapshot_id is None:
                continue
            for assignment in historical_plan.get("assignments") or ():
                resolved = normalized_equipment_assignment(assignment)
                if is_virtual_equipment_assignment(resolved):
                    continue
                try:
                    key = (
                        int(resolved["uid_serial"]),
                        int(resolved["uid_slot"]),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                if min(key) < 1:
                    continue
                # list_loadout_plans is newest-first. Preserve the most recent
                # immutable plan snapshot that actually contained this UID.
                historical_snapshot_by_uid.setdefault(
                    key,
                    int(historical_snapshot_id),
                )
        for _slot, plan in plans.values():
            snapshot_id = int(plan["source_snapshot_id"])
            for assignment in plan.get("assignments") or ():
                resolved = normalized_equipment_assignment(assignment)
                if is_virtual_equipment_assignment(resolved):
                    continue
                try:
                    key = (
                        int(resolved["uid_serial"]),
                        int(resolved["uid_slot"]),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                if min(key) >= 1:
                    assignment_uids_by_snapshot[snapshot_id].add(key)
            last_diff = (plan.get("payload") or {}).get("last_diff") or {}
            for diff_key in ("removed", "added"):
                for diff_item in last_diff.get(diff_key, ()) or ():
                    uid = diff_item.get("uid") if isinstance(diff_item, dict) else diff_item
                    key = _inventory_uid_key(uid)
                    if key is not None:
                        diff_uids_by_snapshot[snapshot_id].add(key)
        inventory_by_snapshot = {
            snapshot_id: {
                (row["uid_serial"], row["uid_slot"]): row
                for row in user_dao.list_inventory_items(
                    snapshot_id,
                    uids=(
                        assignment_uids_by_snapshot[snapshot_id]
                        | diff_uids_by_snapshot[snapshot_id]
                    ),
                )
            }
            for snapshot_id in snapshot_ids
        }
        historical_diff_requests: dict[int, set[tuple[int, int]]] = {}
        for snapshot_id, diff_uids in diff_uids_by_snapshot.items():
            missing_uids = diff_uids - set(inventory_by_snapshot[snapshot_id])
            for uid in missing_uids:
                historical_snapshot_id = historical_snapshot_by_uid.get(uid)
                if historical_snapshot_id is not None:
                    historical_diff_requests.setdefault(
                        historical_snapshot_id,
                        set(),
                    ).add(uid)
        historical_diff_items: dict[tuple[int, int], dict[str, Any]] = {}
        for snapshot_id, uids in historical_diff_requests.items():
            for row in user_dao.list_inventory_items(snapshot_id, uids=uids):
                historical_diff_items[(row["uid_serial"], row["uid_slot"])] = row
        for snapshot_id, diff_uids in diff_uids_by_snapshot.items():
            for uid in diff_uids:
                if uid not in inventory_by_snapshot[snapshot_id] and uid in historical_diff_items:
                    inventory_by_snapshot[snapshot_id][uid] = historical_diff_items[uid]
        shape_cells = {shape["shape_id"]: shape.get("cells") or [] for shape in static_dao.list_shapes()}
        suit_names = {
            str(suit["suit_id"]): str(suit.get("name_zh") or suit["suit_id"]) for suit in static_dao.list_suits()
        }
        attribute_ids = {str(attribute["attribute_id"]) for attribute in static_dao.list_equipment_attributes()}
        displays = {}
        snapshot_sources = {
            snapshot_id: str(
                (user_dao.inventory_snapshot_summary(snapshot_id) or {}).get(
                    "source"
                )
                or ""
            )
            for snapshot_id in snapshot_ids
        }
        role_names = {
            int(row["character_id"]): str(row.get("name_zh") or row["character_id"])
            for row in getattr(static_dao, "list_characters", lambda: ())()
        }
        current_slot_ids = {int(slot["slot_id"]) for slot, _plan in plans.values()}
        visible_character_ids = {
            int(plan["character_id"])
            for _slot, plan in plans.values()
        }
        for slot in visible_slots:
            if int(slot["slot_id"]) in current_slot_ids:
                continue
            if int(slot["character_id"]) not in visible_character_ids:
                continue
            role_name = role_names.get(int(slot["character_id"]), str(slot["character_id"]))
            displays[f"slot:{slot['slot_id']}"] = {
                "_empty_slot": True,
                "_character_id": int(slot["character_id"]),
                "_role_name": role_name,
                "_loadout_slot_id": int(slot["slot_id"]),
                "_loadout_slot_key": str(slot["slot_key"]),
                "_loadout_slot_name": str(slot["slot_name"]),
                "_display_name": role_name if slot["slot_name"] == "主力" else str(slot["slot_name"]),
            }
        for state_key, (slot, plan) in plans.items():
            display = _sqlite_plan_display_state(
                plan,
                user_dao,
                static_dao,
                inventory_by_snapshot=inventory_by_snapshot,
                shape_cells=shape_cells,
                suit_names=suit_names,
                attribute_ids=attribute_ids,
            )
            payload = plan.get("payload") or {}
            role_name = str(payload.get("source_role_name") or plan["character_id"])
            display["_character_id"] = int(plan["character_id"])
            display["_role_name"] = role_name
            display["_loadout_slot_id"] = int(slot["slot_id"])
            display["_loadout_slot_key"] = str(slot["slot_key"])
            display["_loadout_slot_name"] = str(slot["slot_name"])
            display["_display_name"] = role_name if slot["slot_name"] == "主力" else str(slot["slot_name"])
            display["_sqlite_snapshot_source"] = snapshot_sources.get(
                int(plan["source_snapshot_id"]),
                "",
            )
            displays[state_key] = display
        return displays


def _load_game_equipment_display_states(
    database_path,
    *,
    static_database_path=None,
    saved_states=None,
):
    from src.optimizer.contracts import (
        ROLE_BLUEPRINT_LAYOUT,
        ROLE_EQUIPPED_DRIVES,
        ROLE_EQUIPPED_TAPE,
        ROLE_LAST_DIFF,
        ROLE_TOTAL_GRADE,
        ROLE_TOTAL_SCORE,
    )
    from src.services.game_loadout_projection_service import (
        GameLoadoutProjectionService,
    )

    if saved_states is None:
        try:
            saved_states = _load_sqlite_equipment_display_states(
                database_path,
                static_database_path=static_database_path,
            )
        except Exception:
            logger.warning("游戏配装加载计算配装对比数据失败，本次仅展示游戏配装")
            saved_states = {}
    else:
        saved_states = dict(saved_states)

    with UserDataDao(database_path) as user_dao, StaticGameDataDao(static_database_path) as static_dao:
        projection = GameLoadoutProjectionService(user_dao, static_dao).project_current()
        if not projection.supported:
            return {
                "projection": projection,
                "states": {},
                "saved_states": saved_states,
            }
        suit_names = {
            str(suit["suit_id"]): str(suit.get("name_zh") or suit["suit_id"])
            for suit in static_dao.list_suits()
        }
        shape_cells = {
            shape["shape_id"]: shape.get("cells") or []
            for shape in static_dao.list_shapes()
        }
        attribute_ids = {
            str(attribute["attribute_id"])
            for attribute in static_dao.list_equipment_attributes()
        }
        inventory = {
            (int(item["uid_serial"]), int(item["uid_slot"])): dict(item)
            for role in projection.roles
            for item in role.items
        }
        inventory_by_snapshot = {
            int(projection.snapshot_id): inventory
        } if projection.snapshot_id is not None else {}
        states = {}
        for role in projection.roles:
            if role.importable:
                plan = {
                    "plan_id": 0,
                    "source_snapshot_id": role.snapshot_id,
                    "score": None,
                    "payload": {"strategy": "game_inventory"},
                    "assignments": [dict(item) for item in role.assignments],
                    "allocation_locked": False,
                }
                state = _sqlite_plan_display_state(
                    plan,
                    user_dao,
                    static_dao,
                    inventory_by_snapshot=inventory_by_snapshot,
                    shape_cells=shape_cells,
                    suit_names=suit_names,
                    attribute_ids=attribute_ids,
                )
                state.pop("_sqlite_plan_id", None)
            else:
                display_items = [
                    _sqlite_inventory_item_display(item, suit_names)
                    for item in role.items
                ]
                state = {
                    ROLE_BLUEPRINT_LAYOUT: [],
                    ROLE_EQUIPPED_TAPE: next(
                        (
                            item
                            for raw, item in zip(role.items, display_items)
                            if raw.get("kind") == "core"
                        ),
                        None,
                    ),
                    ROLE_EQUIPPED_DRIVES: [
                        item
                        for raw, item in zip(role.items, display_items)
                        if raw.get("kind") == "module"
                    ],
                    ROLE_TOTAL_SCORE: 0.0,
                    ROLE_TOTAL_GRADE: "",
                    ROLE_LAST_DIFF: {},
                    "strategy_mode": "game_inventory",
                }
            state.update({
                "_character_id": role.character_id,
                "_role_name": role.role_name,
                "_display_name": role.role_name,
                "_game_mode": True,
                "_game_projection": role,
                "_game_importable": role.importable,
                "_game_status": role.status,
                "_game_reason": role.reason,
                "_game_imported": role.imported,
                "_game_existing_plan_id": role.existing_plan_id,
                "_game_existing_plan_name": role.existing_plan_name,
                "_game_existing_plan_locked": role.existing_plan_locked,
            })
            comparison_slots = [
                candidate
                for candidate in saved_states.values()
                if isinstance(candidate, dict)
                and candidate.get("_role_name") == role.role_name
            ]
            state["_game_compare_slot_states"] = comparison_slots
            saved_state = next(
                (
                    candidate
                    for candidate in comparison_slots
                    if candidate.get("_loadout_slot_key") == "primary"
                ),
                None,
            )
            if isinstance(saved_state, dict):
                state["_game_saved_state"] = saved_state
            states[role.role_name] = state
        return {
            "projection": projection,
            "states": states,
            "saved_states": saved_states,
        }
