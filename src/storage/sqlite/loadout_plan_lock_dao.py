# 管理活动配装方案的计算保留锁及其装备占用查询。
"""SQLite access methods for allocation-plan reservation locks."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from src.services.virtual_equipment_service import is_virtual_equipment_assignment

from .protocols import UserDataDaoMixinHost
from .user_data_support import UserDataError, UserDataValidationError, _integer, _utc_now


class LoadoutPlanLockDaoMixin(UserDataDaoMixinHost):
    """Own the persisted lock state for active, official allocation plans.

    This is a reservation in the calculator, not the game's equipment-lock
    flag.  A lock can retain a partially filled plan, but it must contain a
    real core and cannot contain a virtual placeholder.
    """

    @staticmethod
    def _is_official_allocation_plan(plan: Mapping[str, Any]) -> bool:
        payload = plan.get("payload")
        role_name = payload.get("source_role_name") if isinstance(payload, Mapping) else None
        return bool(
            isinstance(payload, Mapping)
            and payload.get("schema") == "allocation-official-snapshot-v1"
            and isinstance(role_name, str)
            and role_name.strip()
        )

    @staticmethod
    def _validate_lockable_plan(plan: Mapping[str, Any]) -> None:
        if not plan.get("is_active"):
            raise UserDataValidationError("只能锁定当前活动的配装方案")
        if not LoadoutPlanLockDaoMixin._is_official_allocation_plan(plan):
            raise UserDataValidationError("只有当前配装页的正式角色方案可以锁定")
        assignments = tuple(plan.get("assignments") or ())
        if not assignments:
            raise UserDataValidationError("空方案不能锁定")
        has_real_core = False
        for assignment in assignments:
            raw_assignment = assignment.get("raw_assignment") or assignment
            if is_virtual_equipment_assignment(raw_assignment):
                raise UserDataValidationError("含虚拟占位装备的方案不能锁定")
            try:
                slot = int(assignment["uid_slot"])
                serial = int(assignment["uid_serial"])
            except (KeyError, TypeError, ValueError) as exc:
                raise UserDataValidationError("方案存在无效装备 UID，不能锁定") from exc
            if slot <= 0 or serial <= 0:
                raise UserDataValidationError("方案存在非真实装备，不能锁定")
            if assignment.get("kind") == "core":
                has_real_core = True
        if not has_real_core:
            raise UserDataValidationError("不含真实卡带的方案不能锁定")

    def set_allocation_lock(self, plan_id: int, locked: bool) -> bool:
        """Set a plan reservation lock after validating its visible state."""

        raw_plan_id = _integer(plan_id, "plan_id", minimum=1)
        plan = self.get_loadout_plan(raw_plan_id)
        if plan is None:
            raise UserDataValidationError("未找到要锁定的配装方案")
        target = bool(locked)
        if target:
            self._validate_lockable_plan(plan)
        elif not plan.get("is_active"):
            raise UserDataValidationError("只能解除当前活动方案的锁定")
        if bool(plan.get("allocation_locked")) == target:
            return False
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE loadout_plan
                SET allocation_locked = ?, updated_at_utc = ?
                WHERE plan_id = ? AND is_active = 1 AND updated_at_utc = ?
                """,
                (
                    int(target),
                    _utc_now(),
                    raw_plan_id,
                    str(plan.get("updated_at_utc") or ""),
                ),
            )
            if cursor.rowcount != 1:
                raise UserDataValidationError("配装方案已变化，请刷新后重试")
            connection.commit()
            return True
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法更新配装锁定状态") from exc
        except BaseException:
            connection.rollback()
            raise

    def list_allocation_locked_loadout_plans_by_role(self) -> dict[str, dict[str, Any]]:
        """Return active official plans that reserve equipment for calculation."""

        locked: dict[str, dict[str, Any]] = {}
        for plan in self.list_loadout_plans():
            payload = plan.get("payload")
            role_name = payload.get("source_role_name") if isinstance(payload, Mapping) else None
            if (
                plan.get("is_active")
                and plan.get("allocation_locked")
                and self._is_official_allocation_plan(plan)
                and isinstance(role_name, str)
            ):
                locked[role_name] = plan
        return locked

    def list_allocation_locked_equipment_owners(self) -> list[dict[str, Any]]:
        """Return physical equipment occupied by every active locked plan."""

        return self._rows(
            """
            SELECT item.uid_slot, item.uid_serial, item.kind,
                   plan.plan_id, plan.character_id, plan.updated_at_utc
            FROM loadout_plan_item AS item
            JOIN loadout_plan AS plan USING(plan_id)
            WHERE plan.is_active = 1
              AND plan.allocation_locked = 1
              AND item.uid_slot > 0
            ORDER BY plan.updated_at_utc DESC, plan.plan_id DESC, item.ordinal
            """
        )

    def assert_allocation_lock_invariants(self) -> None:
        """Reject historical duplicate ownership between two locked plans."""

        owners: dict[tuple[int, int], int] = {}
        for plan in self.list_loadout_plans():
            if not plan.get("is_active") or not plan.get("allocation_locked"):
                continue
            for item in plan.get("assignments") or ():
                uid = (int(item["uid_slot"]), int(item["uid_serial"]))
                if uid[0] <= 0:
                    continue
                previous = owners.setdefault(uid, int(plan["plan_id"]))
                if previous != int(plan["plan_id"]):
                    raise UserDataValidationError(
                        "两个锁定方案占用了同一装备，无法自动修复；请先解除其中一个方案的锁定"
                    )

    def assert_active_allocation_locks_preserved(
        self,
        *,
        target_characters: set[int],
        claimed_uids: set[tuple[int, int]],
    ) -> None:
        """Prevent every active-plan writer from overwriting a reservation."""

        locked_plans = [
            plan
            for plan in self.list_loadout_plans()
            if plan["is_active"] and plan.get("allocation_locked")
        ]
        if {
            int(plan["character_id"])
            for plan in locked_plans
        }.intersection(target_characters):
            raise UserDataValidationError(
                "锁定方案不能被重新保存或替换；请先在配装页解除锁定"
            )
        locked_claims = {
            (int(item["uid_slot"]), int(item["uid_serial"]))
            for plan in locked_plans
            for item in plan["assignments"]
            if int(item["uid_slot"]) > 0
        }
        conflicting_uids = sorted(claimed_uids.intersection(locked_claims))
        if conflicting_uids:
            labels = ", ".join(
                f"({slot}, {serial})" for slot, serial in conflicting_uids[:3]
            )
            raise UserDataValidationError(f"不能借用锁定方案中的装备：{labels}")
