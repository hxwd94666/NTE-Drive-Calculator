# 按配装槽位构建计算结果的装备差异基线。
"""Keep allocation comparisons scoped to the same named loadout slot."""

from __future__ import annotations

from typing import Any

from src.optimizer.contracts import EQUIP_UID, ROLE_EQUIPPED_DRIVES, ROLE_EQUIPPED_TAPE
from src.optimizer.plan_diff import build_plan_diff
from src.storage.sqlite.user_data_dao import UserDataDao


def loadout_plan_state(plan: dict[str, Any]) -> dict[str, Any]:
    """Project one persisted plan into the minimal plan-diff baseline."""

    tape = None
    drives = []
    for assignment in plan.get("assignments") or []:
        kind = str(assignment.get("kind") or "")
        try:
            uid = (
                f"nte-{'module' if kind == 'module' else 'core'}-"
                f"{int(assignment['uid_slot'])}-{int(assignment['uid_serial'])}"
            )
        except (KeyError, TypeError, ValueError):
            continue
        if kind == "core":
            tape = {EQUIP_UID: uid}
        elif kind == "module":
            drives.append({EQUIP_UID: uid})
    return {
        ROLE_EQUIPPED_TAPE: tape,
        ROLE_EQUIPPED_DRIVES: drives,
    }


def single_slot_loadout_state(user_dao: UserDataDao) -> dict[str, dict[str, Any]]:
    """Return a baseline only where one visible slot makes it unambiguous."""

    state: dict[str, dict[str, Any]] = {}
    for role_name, plan in user_dao.list_active_loadout_plans_by_role().items():
        character_id = int(plan["character_id"])
        if len(user_dao.list_loadout_slots(character_id)) != 1:
            continue
        state[role_name] = loadout_plan_state(plan)
    return state


def selected_slot_plan_diff(
    user_dao: UserDataDao,
    final_plan: dict[str, Any],
    targets: dict[str, tuple[int, int]],
) -> dict[str, dict[str, Any]]:
    """Compare every calculated role exclusively with its selected save slot."""

    state: dict[str, dict[str, Any]] = {}
    for role_name, (character_id, slot_id) in targets.items():
        slot = user_dao.get_loadout_slot(int(slot_id))
        if slot is None or int(slot["character_id"]) != int(character_id):
            raise RuntimeError(f"保存槽位不存在或不属于角色 [{role_name}]")
        plan = slot.get("current_plan")
        if isinstance(plan, dict):
            state[role_name] = loadout_plan_state(plan)
    return build_plan_diff(state, final_plan)
