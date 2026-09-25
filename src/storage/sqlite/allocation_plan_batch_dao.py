# 原子保存计算方案并解除其他角色当前槽位对借出装备的占用。
from __future__ import annotations

from src.services.loadout_equipment_identity import source_snapshots_share_equipment_uids
from src.services.virtual_equipment_service import is_virtual_equipment_assignment

from .protocols import UserDataDaoMixinHost
from .user_data_support import UserDataError, UserDataValidationError, _integer


class AllocationPlanBatchDaoMixin(UserDataDaoMixinHost):
    def save_calculated_loadout_plans(self, plans, *, checkpoint, validate=None):
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
            inventory_kinds = {}
            for row in plans:
                slot_id = _integer(row.get("slot_id"), "slot_id", minimum=1)
                slot = self.get_loadout_slot(slot_id)
                if (slot is None or slot["is_archived"] or slot_id in slots
                        or int(slot["character_id"]) != int(row["character_id"])):
                    raise UserDataValidationError("计算方案目标槽位无效或重复")
                slots[slot_id] = slot
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
            for row in plans:
                checkpoint()
                slot_id = int(row["slot_id"])
                arguments = {key: row[key] for key in (
                    "name", "character_id", "assignments", "source_snapshot_id", "status", "score", "payload",
                )}
                plan_id = self.save_loadout_plan(**arguments, slot_id=slot_id, is_active=False)
                if slots[slot_id]["slot_key"] == "primary":
                    connection.execute("UPDATE loadout_plan SET is_active = 0 WHERE character_id = ?", (row["character_id"],))
                    connection.execute("UPDATE loadout_plan SET is_active = 1 WHERE plan_id = ?", (plan_id,))
                result.append(plan_id)
            checkpoint()
            connection.commit()
            return tuple(result)
        except BaseException:
            connection.rollback()
            raise
