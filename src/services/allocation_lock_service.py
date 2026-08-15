# 将已锁定配装转换为本次计算的不可变装备保留快照。
"""Application service for allocation-plan reservation locks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.services.virtual_equipment_service import (
    is_virtual_equipment_assignment,
    normalized_equipment_assignment,
)
from src.storage.sqlite.user_data_support import UserDataValidationError

if TYPE_CHECKING:
    from src.storage.sqlite.user_data_dao import UserDataDao


@dataclass(frozen=True)
class AllocationLockSnapshot:
    """Frozen account-plan reservations for one immutable inventory snapshot."""

    inventory_snapshot_id: int
    locked_role_names: frozenset[str]
    reserved_uids: frozenset[str]
    plan_revisions: tuple[tuple[int, str], ...]


def _display_uid(kind: str, slot: int, serial: int) -> str:
    prefix = "module" if kind == "module" else "core"
    return f"nte-{prefix}-{slot}-{serial}"


def _role_has_all_slots_locked(user_dao: "UserDataDao", character_id: int) -> bool:
    """Return whether every visible slot for one character is reservation-locked.

    A lock always reserves the physical equipment in its own slot.  It only
    removes a character from a new allocation when all of that character's
    visible slots are locked.  Keep the legacy fallback for databases opened
    before the named-slot migration is available.
    """

    list_slots = getattr(user_dao, "list_loadout_slots", None)
    if not callable(list_slots):
        return True
    slots = list_slots(int(character_id))
    return bool(slots) and all(
        bool((slot.get("current_plan") or {}).get("allocation_locked"))
        for slot in slots
    )


def build_allocation_lock_snapshot(
    user_dao: "UserDataDao",
    *,
    inventory_snapshot_id: int,
) -> AllocationLockSnapshot:
    """Validate every persisted lock against the calculation's fixed snapshot.

    The source snapshot of a saved plan may be old.  The reservation is valid
    only when every real assignment still appears, with the same kind, in the
    snapshot being calculated.  Both nte-core and visual snapshots use the
    same inventory table and therefore follow exactly this rule.
    """

    current_items = {
        (int(item["uid_slot"]), int(item["uid_serial"])): str(item["kind"])
        for item in user_dao.list_inventory_items(inventory_snapshot_id)
    }
    locked_plans = user_dao.list_allocation_locked_loadout_plans()
    reserved_uids: set[str] = set()
    revisions: list[tuple[int, str]] = []
    locked_role_names: set[str] = set()
    fully_locked_roles: dict[int, bool] = {}
    for plan in sorted(locked_plans, key=lambda row: int(row["plan_id"])):
        payload = plan.get("payload") or {}
        role_name = str(payload.get("source_role_name") or "")
        if not role_name:
            raise UserDataValidationError("锁定方案缺少角色名称，请解除锁定后重新保存")
        character_id = int(plan["character_id"])
        if fully_locked_roles.setdefault(
            character_id,
            _role_has_all_slots_locked(user_dao, character_id),
        ):
            locked_role_names.add(role_name)
        assignments = tuple(plan.get("assignments") or ())
        if not assignments:
            raise UserDataValidationError(f"锁定方案 [{role_name}] 为空，请解除锁定后重试")
        for assignment in assignments:
            resolved_assignment = normalized_equipment_assignment(assignment)
            if is_virtual_equipment_assignment(resolved_assignment):
                raise UserDataValidationError(
                    f"锁定方案 [{role_name}] 含虚拟装备，请解除锁定后重试"
                )
            try:
                slot = int(assignment["uid_slot"])
                serial = int(assignment["uid_serial"])
                kind = str(assignment["kind"])
            except (KeyError, TypeError, ValueError) as exc:
                raise UserDataValidationError(
                    f"锁定方案 [{role_name}] 含无效装备 UID，请解除锁定后重试"
                ) from exc
            if kind not in {"module", "core"} or slot <= 0 or serial <= 0:
                raise UserDataValidationError(
                    f"锁定方案 [{role_name}] 含非真实装备，请解除锁定后重试"
                )
            if current_items.get((slot, serial)) != kind:
                raise UserDataValidationError(
                    f"锁定方案 [{role_name}] 的装备 ({slot}, {serial}) 不在当前稳定背包快照中；"
                    "请同步背包后检查方案，或解除锁定。"
                )
            reserved_uids.add(_display_uid(kind, slot, serial))
        revisions.append((int(plan["plan_id"]), str(plan.get("updated_at_utc") or "")))
    return AllocationLockSnapshot(
        inventory_snapshot_id=int(inventory_snapshot_id),
        locked_role_names=frozenset(locked_role_names),
        reserved_uids=frozenset(reserved_uids),
        plan_revisions=tuple(revisions),
    )


def verify_allocation_lock_snapshot(
    user_dao: "UserDataDao",
    snapshot: AllocationLockSnapshot,
) -> None:
    """Reject saving a calculation if the user changed a reservation meanwhile."""

    current = build_allocation_lock_snapshot(
        user_dao,
        inventory_snapshot_id=snapshot.inventory_snapshot_id,
    )
    if current != snapshot:
        raise UserDataValidationError(
            "配装锁定状态已在计算期间变化，请重新计算后再保存"
        )


def filter_allocation_request_for_locks(
    selected_roles: list[str],
    priority_groups: object,
    snapshot: AllocationLockSnapshot,
) -> tuple[list[str], list[list[str]] | None]:
    """Exclude locked roles while retaining group order for the unlocked roles."""

    selected = [role for role in selected_roles if role not in snapshot.locked_role_names]
    if priority_groups is None:
        return selected, None
    filtered_groups: list[list[str]] = []
    for raw_group in priority_groups if isinstance(priority_groups, (list, tuple)) else ():
        if not isinstance(raw_group, (list, tuple)):
            continue
        group = [
            str(role) for role in raw_group
            if str(role) in selected and str(role) not in snapshot.locked_role_names
        ]
        if group:
            filtered_groups.append(group)
    return selected, filtered_groups
