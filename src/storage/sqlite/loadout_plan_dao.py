# 管理已保存配装方案的 SQLite 访问方法。
from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from .user_data_support import (
    SCHEMA_VERSION,
    UserDataError,
    UserDataValidationError,
    _decoded,
    _integer,
    _utc_now,
)



_ACTIVE_ROLE_PLAN_SCHEMAS = frozenset({
    "allocation-official-snapshot-v1",
    "game-observed-loadout-v1",
})


from .loadout_plan_write_dao import LoadoutPlanWriteDaoMixin


class LoadoutPlanDaoMixin(LoadoutPlanWriteDaoMixin):
    def list_loadout_plans(self, character_id: int | None = None) -> list[dict[str, Any]]:
        where = "" if character_id is None else "WHERE character_id = ?"
        parameters = () if character_id is None else (_integer(character_id, "character_id", minimum=1),)
        rows = self._rows(
            f"""
            SELECT plan_id, name, character_id, slot_id, source_snapshot_id, status,
                   score, payload_json, is_active, allocation_locked,
                   created_at_utc, updated_at_utc
            FROM loadout_plan {where}
            ORDER BY updated_at_utc DESC, plan_id DESC
            """,
            parameters,
        )
        for row in rows:
            row["is_active"] = bool(row["is_active"])
            row["allocation_locked"] = bool(row["allocation_locked"])
            row["payload"] = _decoded(row.pop("payload_json"), {})
            row["assignments"] = self._rows(
                """
                SELECT ordinal, uid_serial, uid_slot, kind, target_row,
                       target_column, rotation, raw_assignment_json
                FROM loadout_plan_item WHERE plan_id = ? ORDER BY ordinal
                """,
                (row["plan_id"],),
            )
            for assignment in row["assignments"]:
                assignment["raw_assignment"] = _decoded(
                    assignment.pop("raw_assignment_json"), {}
                )
        return rows

    def get_loadout_plan(self, plan_id: int) -> dict[str, Any] | None:
        """按方案 ID 返回完整方案；读取格式与 ``list_loadout_plans`` 一致。"""

        raw_plan_id = _integer(plan_id, "plan_id", minimum=1)
        return next(
            (
                plan
                for plan in self.list_loadout_plans()
                if plan["plan_id"] == raw_plan_id
            ),
            None,
        )

    def get_active_loadout_plan_for_role(self, role_name: str) -> dict[str, Any] | None:
        """返回指定显示角色名当前可执行的 SQLite 方案。"""

        raw_role_name = str(role_name).strip()
        if not raw_role_name:
            raise UserDataValidationError("角色名称不能为空")
        return next(
            (
                plan
                for plan in self.list_loadout_plans()
                if plan["is_active"]
                and isinstance(plan.get("payload"), Mapping)
                and plan["payload"].get("schema") in _ACTIVE_ROLE_PLAN_SCHEMAS
                and plan["payload"].get("source_role_name") == raw_role_name
            ),
            None,
        )

    def list_active_loadout_plans_by_role(self) -> dict[str, dict[str, Any]]:
        """返回当前所有带显示角色名的可执行 SQLite 方案。"""

        plans: dict[str, dict[str, Any]] = {}
        for plan in self.list_loadout_plans():
            payload = plan.get("payload")
            role_name = payload.get("source_role_name") if isinstance(payload, Mapping) else None
            if (
                plan["is_active"]
                and isinstance(payload, Mapping)
                and payload.get("schema") in _ACTIVE_ROLE_PLAN_SCHEMAS
                and isinstance(role_name, str)
                and role_name.strip()
            ):
                plans.setdefault(role_name, plan)
        return plans

    def list_active_loadout_equipment_owners(self) -> list[dict[str, Any]]:
        """Return real native UIDs and their owners from active saved plans."""

        return self._rows(
            """
            SELECT item.uid_slot, item.uid_serial, item.kind,
                   plan.plan_id, plan.character_id
            FROM loadout_plan_item AS item
            JOIN loadout_plan AS plan USING(plan_id)
            WHERE plan.is_active = 1
              AND item.uid_slot > 0
            ORDER BY plan.updated_at_utc DESC, plan.plan_id DESC, item.ordinal
            """
        )

    def deactivate_loadout_plan(self, plan_id: int) -> bool:
        """Detach one current slot plan while retaining its immutable history."""

        raw_plan_id = _integer(plan_id, "plan_id", minimum=1)
        plan = self.get_loadout_plan(raw_plan_id)
        if plan is None:
            return False
        if plan.get("allocation_locked") and self.is_current_loadout_slot_plan(raw_plan_id):
            raise UserDataValidationError("锁定方案不能删除；请先解除锁定")
        connection = self._db()
        now = _utc_now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            detached = connection.execute(
                """
                UPDATE role_loadout_slot
                SET current_plan_id = NULL, updated_at_utc = ?
                WHERE current_plan_id = ? AND is_archived = 0
                """,
                (now, raw_plan_id),
            )
            connection.execute(
                "UPDATE loadout_plan SET is_active = 0, updated_at_utc = ? WHERE plan_id = ?",
                (now, raw_plan_id),
            )
            connection.commit()
            return detached.rowcount > 0
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法删除配装方案") from exc

    def summary(self) -> dict[str, Any]:
        snapshot_count = self._one(
            "SELECT COUNT(*) AS count FROM inventory_snapshot"
        )
        loadout_plan_count = self._one(
            "SELECT COUNT(*) AS count FROM loadout_plan"
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "database_path": str(self.database_path),
            "profile": self.profile(),
            "sync_settings": self.get_sync_settings(),
            "inventory": self.current_inventory_summary(),
            "snapshot_count": int((snapshot_count or {}).get("count", 0)),
            "loadout_plan_count": int(
                (loadout_plan_count or {}).get("count", 0)
            ),
        }

    def integrity_check(self) -> dict[str, Any]:
        quick_check = [row["quick_check"] for row in self._rows("PRAGMA quick_check")]
        foreign_key_errors = self._rows("PRAGMA foreign_key_check")
        return {
            "ok": quick_check == ["ok"] and not foreign_key_errors,
            "quick_check": quick_check,
            "foreign_key_errors": foreign_key_errors,
        }
