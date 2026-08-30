# 管理已保存配装方案的 SQLite 访问方法。
from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .user_data_support import (
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
    def _decode_loadout_plan_rows(
        self,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not rows:
            return rows
        plan_ids = [int(row["plan_id"]) for row in rows]
        placeholders = ",".join("?" for _plan_id in plan_ids)
        assignments = self._rows(
            f"""
            SELECT plan_id, ordinal, uid_serial, uid_slot, kind, target_row,
                   target_column, rotation, raw_assignment_json
            FROM loadout_plan_item
            WHERE plan_id IN ({placeholders})
            ORDER BY plan_id, ordinal
            """,
            plan_ids,
        )
        assignments_by_plan: dict[int, list[dict[str, Any]]] = {
            plan_id: [] for plan_id in plan_ids
        }
        for assignment in assignments:
            plan_id = int(assignment.pop("plan_id"))
            assignment["raw_assignment"] = _decoded(
                assignment.pop("raw_assignment_json"), {}
            )
            assignments_by_plan[plan_id].append(assignment)
        for row in rows:
            row["is_active"] = bool(row["is_active"])
            row["allocation_locked"] = bool(row["allocation_locked"])
            row["payload"] = _decoded(row.pop("payload_json"), {})
            row["assignments"] = assignments_by_plan[int(row["plan_id"])]
        return rows

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
        return self._decode_loadout_plan_rows(rows)

    def get_loadout_plans(self, plan_ids: Sequence[int]) -> list[dict[str, Any]]:
        """Return complete plans for a bounded ID set without scanning history."""

        normalized = tuple(dict.fromkeys(
            _integer(plan_id, "plan_id", minimum=1) for plan_id in plan_ids
        ))
        if not normalized:
            return []
        placeholders = ",".join("?" for _plan_id in normalized)
        rows = self._rows(
            f"""
            SELECT plan_id, name, character_id, slot_id, source_snapshot_id, status,
                   score, payload_json, is_active, allocation_locked,
                   created_at_utc, updated_at_utc
            FROM loadout_plan
            WHERE plan_id IN ({placeholders})
            """,
            normalized,
        )
        by_id = {
            int(row["plan_id"]): row
            for row in self._decode_loadout_plan_rows(rows)
        }
        return [by_id[plan_id] for plan_id in normalized if plan_id in by_id]

    def get_loadout_plan(self, plan_id: int) -> dict[str, Any] | None:
        """按方案 ID 返回完整方案；读取格式与 ``list_loadout_plans`` 一致。"""

        raw_plan_id = _integer(plan_id, "plan_id", minimum=1)
        plans = self.get_loadout_plans((raw_plan_id,))
        return plans[0] if plans else None

    def get_active_loadout_plan_for_role(self, role_name: str) -> dict[str, Any] | None:
        """返回指定显示角色名当前槽位中的可执行 SQLite 方案。"""

        raw_role_name = str(role_name).strip()
        if not raw_role_name:
            raise UserDataValidationError("角色名称不能为空")
        return next(
            (
                plan_row["plan"]
                for plan_row in self.list_current_loadout_slot_plans()
                if isinstance((plan := plan_row.get("plan")), Mapping)
                and isinstance(plan.get("payload"), Mapping)
                and plan["payload"].get("schema") in _ACTIVE_ROLE_PLAN_SCHEMAS
                and plan["payload"].get("source_role_name") == raw_role_name
            ),
            None,
        )

    def list_active_loadout_plans_by_role(self) -> dict[str, dict[str, Any]]:
        """返回所有可见当前槽位中带显示角色名的可执行 SQLite 方案。"""

        plans: dict[str, dict[str, Any]] = {}
        for plan_row in self.list_current_loadout_slot_plans():
            plan = plan_row.get("plan")
            if not isinstance(plan, Mapping):
                continue
            payload = plan.get("payload")
            role_name = payload.get("source_role_name") if isinstance(payload, Mapping) else None
            if (
                isinstance(payload, Mapping)
                and payload.get("schema") in _ACTIVE_ROLE_PLAN_SCHEMAS
                and isinstance(role_name, str)
                and role_name.strip()
            ):
                plans.setdefault(role_name, dict(plan))
        return plans

    def list_active_loadout_equipment_owners(self) -> list[dict[str, Any]]:
        """Return real native UIDs from every visible slot's current plan.

        ``loadout_plan.is_active`` is a legacy history flag and is not the
        ownership source after named slots were introduced.  In particular, a
        deleted current plan can leave an older immutable plan in history.
        Only ``role_loadout_slot.current_plan_id`` may reserve an item.
        """

        return self._rows(
            """
            SELECT item.uid_slot, item.uid_serial, item.kind,
                   plan.plan_id, plan.character_id, slot.slot_id, slot.slot_name
            FROM loadout_plan_item AS item
            JOIN role_loadout_slot AS slot ON slot.current_plan_id = item.plan_id
            JOIN loadout_plan AS plan ON plan.plan_id = item.plan_id
            WHERE slot.is_archived = 0 AND item.uid_slot > 0
            ORDER BY slot.updated_at_utc DESC, slot.slot_id DESC, item.ordinal
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
        schema_row = self._one(
            "SELECT MAX(version) AS version FROM schema_migration"
        )
        snapshot_count = self._one(
            "SELECT COUNT(*) AS count FROM inventory_snapshot"
        )
        loadout_plan_count = self._one(
            "SELECT COUNT(*) AS count FROM loadout_plan"
        )
        return {
            "schema_version": int((schema_row or {}).get("version", 0)),
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
