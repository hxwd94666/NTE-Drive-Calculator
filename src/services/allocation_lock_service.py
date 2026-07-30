# 将已锁定配装转换为本次计算的不可变装备保留快照。
"""Application service for allocation-plan reservation locks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.services.virtual_equipment_service import is_virtual_equipment_assignment
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
    plans_by_role = user_dao.list_allocation_locked_loadout_plans_by_role()
    reserved_uids: set[str] = set()
    revisions: list[tuple[int, str]] = []
    for role_name, plan in sorted(plans_by_role.items()):
        assignments = tuple(plan.get("assignments") or ())
        if not assignments:
            raise UserDataValidationError(f"锁定方案 [{role_name}] 为空，请解除锁定后重试")
        has_real_core = False
        for assignment in assignments:
            raw_assignment = assignment.get("raw_assignment") or assignment
            if is_virtual_equipment_assignment(raw_assignment):
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
            has_real_core = has_real_core or kind == "core"
            reserved_uids.add(_display_uid(kind, slot, serial))
        if not has_real_core:
            raise UserDataValidationError(
                f"锁定方案 [{role_name}] 不含真实卡带，请解除锁定后重试"
            )
        revisions.append((int(plan["plan_id"]), str(plan.get("updated_at_utc") or "")))
    return AllocationLockSnapshot(
        inventory_snapshot_id=int(inventory_snapshot_id),
        locked_role_names=frozenset(plans_by_role),
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
