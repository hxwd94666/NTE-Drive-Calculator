# 原子保存计算方案并解除其他角色当前槽位对借出装备的占用。
from __future__ import annotations

from contextlib import nullcontext

from src.domain.equipment.loadout_equipment_identity import source_snapshots_share_equipment_uids
from src.domain.equipment.virtual_equipment import is_virtual_equipment_assignment

from src.storage.sqlite.protocols import UserDataDaoMixinHost
from src.storage.sqlite.equipment.loadout_comparison_guard import assert_comparison_baseline
from src.storage.sqlite.user_data_support import UserDataError, UserDataValidationError, _integer, _utc_now


class AllocationPlanBatchDaoMixin(UserDataDaoMixinHost):
    """在同一账号事务中创建计算槽位并保存整批冻结方案，失败不留部分角色结果。"""

    def _create_calculated_slot_in_transaction(self, character_id, name):
        """Create the first visible slot only inside the calculated-plan transaction."""
        connection = self._db()
        rows = connection.execute(
            "SELECT slot_key, is_archived, sort_order FROM role_loadout_slot "
            "WHERE character_id = ?", (character_id,),
        ).fetchall()
        if any(not row[1] for row in rows):
            raise UserDataValidationError("目标配装槽位已改变，请重新选择")
        keys = {str(row[0]) for row in rows}
        if "primary" not in keys:
            key = "primary"
        else:
            number = 1
            while f"slot-{number}" in keys:
                number += 1
            key = f"slot-{number}"
        now = _utc_now()
        cursor = connection.execute(
            "INSERT INTO role_loadout_slot("
            "character_id, slot_key, slot_name, sort_order, current_plan_id, "
            "is_archived, created_at_utc, updated_at_utc) "
            "VALUES (?, ?, ?, ?, NULL, 0, ?, ?)",
            (
                character_id, key, self._normalize_slot_name(name),
                max((int(row[2]) for row in rows), default=-1) + 1, now, now,
            ),
        )
        if cursor.lastrowid is None:
            raise UserDataError("创建目标配装槽位后未返回 slot_id")
        return int(cursor.lastrowid)

    def save_calculated_loadout_plans(
        self, plans, *, checkpoint, validate=None, commit_context=None, activate_primary=True,
    ):
        """Persist targets and release other current owners in one transaction."""
        if not plans:
            raise UserDataValidationError("至少需要保存一个计算方案")
        connection = self._db()
        if connection.in_transaction:
            raise UserDataError("计算方案批量保存不能嵌套事务")
        try:
            connection.execute("BEGIN IMMEDIATE")
            checkpoint()
            if validate is not None:
                validate()
            slots, claims, summaries = {}, {}, {}
            resolved_plans = []
            created_slots = set()
            inventory_kinds = {}
            for source in plans:
                row = dict(source)
                if row.get("slot_id") is None:
                    name = row.get("create_slot_name")
                    if name is None:
                        raise UserDataValidationError("新配装槽位缺少角色名称")
                    row["slot_id"] = self._create_calculated_slot_in_transaction(
                        _integer(row["character_id"], "character_id", minimum=1), name,
                    )
                    created_slots.add(int(row["slot_id"]))
                slot_id = _integer(row.get("slot_id"), "slot_id", minimum=1)
                slot = self.get_loadout_slot(slot_id)
                if (slot is None or slot["is_archived"] or slot_id in slots
                        or int(slot["character_id"]) != int(row["character_id"])):
                    raise UserDataValidationError("计算方案目标槽位无效或重复")
                slots[slot_id] = slot
                assert_comparison_baseline(self, slot_id, row.get("comparison_baseline"))
                resolved_plans.append(row)
                self.assert_loadout_slot_save_allowed(
                    slot_id, row["assignments"], source_snapshot_id=row["source_snapshot_id"],
                )
                snapshot_id = int(row["source_snapshot_id"])
                if snapshot_id not in summaries:
                    summaries[snapshot_id] = self.inventory_snapshot_summary(snapshot_id) or {}
                if not summaries[snapshot_id].get("complete"):
                    raise UserDataValidationError("计算方案来源背包快照不完整")
                if snapshot_id not in inventory_kinds:
                    inventory_kinds[snapshot_id] = {
                        (int(item["uid_slot"]), int(item["uid_serial"])): item["kind"]
                        for item in connection.execute(
                            "SELECT uid_slot, uid_serial, kind FROM inventory_item WHERE snapshot_id = ?",
                            (snapshot_id,),
                        )
                    }
                for item in row["assignments"]:
                    if is_virtual_equipment_assignment(item):
                        continue
                    uid = (int(item["uid_slot"]), int(item["uid_serial"]))
                    if inventory_kinds[snapshot_id].get(uid) != item["kind"]:
                        raise UserDataValidationError("装备不在方案冻结的背包快照中")
                    previous = claims.get(uid)
                    if previous is not None and int(previous["character_id"]) != int(row["character_id"]):
                        raise UserDataValidationError("计算方案之间不能重复使用同一真实装备")
                    claims[uid] = row

            owners = []
            for current in self.list_current_loadout_slot_plans():
                slot, plan = current["slot"], current["plan"]
                if int(slot["slot_id"]) in slots or plan.get("source_snapshot_id") is None:
                    continue
                snapshot_id = int(plan["source_snapshot_id"])
                if snapshot_id not in summaries:
                    summaries[snapshot_id] = self.inventory_snapshot_summary(snapshot_id) or {}
                removed, receivers = set(), {}
                for item in plan["assignments"]:
                    uid = (int(item["uid_slot"]), int(item["uid_serial"]))
                    target = claims.get(uid)
                    if target is None or int(target["character_id"]) == int(slot["character_id"]):
                        continue
                    target_snapshot = int(target["source_snapshot_id"])
                    if source_snapshots_share_equipment_uids(
                        snapshot_id, summaries[snapshot_id].get("source"),
                        target_snapshot, summaries[target_snapshot].get("source"),
                    ):
                        removed.add(uid)
                        receivers[int(target["slot_id"])] = int(target["character_id"])
                if not removed:
                    continue
                if plan.get("allocation_locked"):
                    raise UserDataValidationError("不能借用锁定槽位方案中的装备")
                owners.append((slot, plan, removed, receivers))

            # Share one frozen inventory lookup among all released owners too.
            inventories = {}
            for slot, plan, removed, receivers in owners:
                checkpoint()
                snapshot_id = int(plan["source_snapshot_id"])
                if snapshot_id not in inventories:
                    inventories[snapshot_id] = {
                        (int(item["uid_slot"]), int(item["uid_serial"])): item
                        for item in self.list_inventory_items(snapshot_id)
                    }
                target_slot, target_character = next(iter(receivers.items()))
                self._save_released_owner_slot(
                    slot, plan, removed, received_by_slot_id=target_slot,
                    received_by_character_id=target_character,
                    frozen_inventory=inventories[snapshot_id], received_by_slots=receivers,
                )
                current_id = self.get_loadout_slot(int(slot["slot_id"]))["current_plan_id"]
                connection.execute("UPDATE loadout_plan SET is_active = 0 WHERE slot_id = ?", (slot["slot_id"],))
                if plan["is_active"]:
                    connection.execute("UPDATE loadout_plan SET is_active = 1 WHERE plan_id = ?", (current_id,))

            result = []
            for row in resolved_plans:
                checkpoint()
                slot_id = int(row["slot_id"])
                arguments = {key: row[key] for key in (
                    "name", "character_id", "assignments", "source_snapshot_id", "status", "score", "payload",
                )}
                plan_id = self.save_loadout_plan(**arguments, slot_id=slot_id, is_active=False)
                # 显式加权槽位沿用仅推进 current_plan 的语义；其他正式入口默认仍激活主槽。
                if activate_primary and (slots[slot_id]["slot_key"] == "primary" or slot_id in created_slots):
                    connection.execute("UPDATE loadout_plan SET is_active = 0 WHERE character_id = ?", (row["character_id"],))
                    connection.execute("UPDATE loadout_plan SET is_active = 1 WHERE plan_id = ?", (plan_id,))
                result.append(plan_id)
            # 可选提交接受门禁只包住原有末端检查与commit，不改变借出/历史算法。
            with commit_context() if commit_context is not None else nullcontext():
                checkpoint()
                connection.commit()
            return tuple(result)
        except BaseException:
            connection.rollback()
            raise
