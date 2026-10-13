# 在同一事务内复核配装管理的冻结身份及锁，再原子修改槽位或方案指针。
from __future__ import annotations

from src.domain.equipment.loadout_management import ManagedSlot, LoadoutMutationResult
from ..protocols import UserDataDaoMixinHost
from ..user_data_support import UserDataValidationError, _utc_now
from .loadout_slot_dao import MAX_VISIBLE_LOADOUT_SLOTS, PRIMARY_LOADOUT_SLOT_KEY


class LoadoutManagementDaoMixin(UserDataDaoMixinHost):
    def mutate_loadout_management(self, mutation, *, checkpoint, commit_context):
        connection = self._db()
        try:
            checkpoint()
            connection.execute("BEGIN IMMEDIATE")
            rows = self.assert_loadout_management_snapshot(mutation.snapshot)
            scope = mutation.snapshot.character_id
            checkpoint()
            action, slot_id = mutation.action, mutation.slot_id
            row = next((row for row in rows if row["slot_id"] == slot_id), None)
            now = _utc_now()
            if action == "clear":
                if scope is not None:
                    raise UserDataValidationError("清空配装缺少完整冻结范围")
                skipped = []
                for current in rows:
                    checkpoint()
                    plan = current.get("current_plan") or {}
                    if not plan:
                        continue
                    if plan.get("allocation_locked"):
                        skipped.append(f"{(plan.get('payload') or {}).get('source_role_name') or '未知角色'} · {current['slot_name']}")
                    else:
                        self._detach_managed_plan(current, now)
                result = LoadoutMutationResult(action, skipped=tuple(skipped))
            elif action == "create":
                if scope is None or not rows:
                    raise UserDataValidationError("新增槽位缺少正式角色身份")
                if len(rows) >= MAX_VISIBLE_LOADOUT_SLOTS:
                    raise UserDataValidationError(f"每个角色最多创建 {MAX_VISIBLE_LOADOUT_SLOTS} 个配装槽位")
                name = self._normalize_slot_name(mutation.name)
                key = self._normalize_slot_key(self._next_slot_key(scope))
                order = connection.execute(
                    "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM role_loadout_slot WHERE character_id = ?", (scope,),
                ).fetchone()[0]
                cursor = connection.execute(
                    "INSERT INTO role_loadout_slot(character_id, slot_key, slot_name, sort_order, current_plan_id, is_archived, created_at_utc, updated_at_utc) VALUES (?, ?, ?, ?, NULL, 0, ?, ?)",
                    (scope, key, name, order, now, now),
                )
                result = LoadoutMutationResult(action, slot_id=int(cursor.lastrowid))
            else:
                if row is None or scope is None:
                    raise UserDataValidationError("当前配装槽位已失效")
                plan = row.get("current_plan") or {}
                if action == "rename":
                    name = self._normalize_slot_name(mutation.name)
                    connection.execute("UPDATE role_loadout_slot SET slot_name = ?, updated_at_utc = ? WHERE slot_id = ?", (name, now, slot_id))
                    result = LoadoutMutationResult(action, slot_id)
                elif action == "lock":
                    if not plan:
                        raise UserDataValidationError("未找到该槽位的当前配装方案")
                    locked = not bool(plan.get("allocation_locked"))
                    if locked:
                        self._validate_lockable_plan(plan, is_current_slot_plan=True)
                    connection.execute("UPDATE loadout_plan SET allocation_locked = ?, updated_at_utc = ? WHERE plan_id = ?", (int(locked), now, plan["plan_id"]))
                    self.assert_allocation_lock_invariants()
                    result = LoadoutMutationResult(action, slot_id, locked)
                elif action in {"delete", "archive"}:
                    if plan.get("allocation_locked"):
                        raise UserDataValidationError("锁定方案不能删除或归档；请先解除锁定")
                    if action == "delete":
                        if not plan:
                            raise UserDataValidationError("当前方案已失效")
                        self._detach_managed_plan(row, now)
                    else:
                        if len(rows) <= 1:
                            raise UserDataValidationError("至少保留一个配装槽位")
                        self._archive_managed_slot(row, rows, now)
                    result = LoadoutMutationResult(action, slot_id)
                else:
                    raise UserDataValidationError("未知配装管理操作")
            # BEGIN内的所有改动都还未提交。撤权在进入末端接受点前生效；
            # 接受点之后由owner等物理终态，不以取消消息伪称已经回滚。
            with commit_context():
                connection.commit()
            return result
        except BaseException:
            connection.rollback()
            raise

    def assert_loadout_management_snapshot(self, snapshot):
        """供已持有写事务的保存入口复核冻结槽位、锁和当前方案身份。"""
        rows = self.list_visible_loadout_slots_with_plans()
        if snapshot.character_id is not None:
            rows = [row for row in rows if int(row["character_id"]) == snapshot.character_id]
        if tuple(ManagedSlot.from_row(row) for row in rows) != snapshot.slots:
            raise UserDataValidationError("配装槽位或锁状态已变化，请刷新后重试")
        return rows

    def _detach_managed_plan(self, row, now):
        self._db().execute("UPDATE role_loadout_slot SET current_plan_id = NULL, updated_at_utc = ? WHERE slot_id = ?", (now, row["slot_id"]))
        self._db().execute("UPDATE loadout_plan SET is_active = 0, updated_at_utc = ? WHERE plan_id = ?", (now, row["current_plan_id"]))

    def _archive_managed_slot(self, row, rows, now):
        connection = self._db()
        slot_id = row["slot_id"]
        if row["slot_key"] == PRIMARY_LOADOUT_SLOT_KEY:
            replacement = next(other for other in rows if other["slot_id"] != slot_id)
            connection.execute("UPDATE role_loadout_slot SET slot_key = ?, updated_at_utc = ? WHERE slot_id = ?", (f"archived-{slot_id}", now, slot_id))
            connection.execute("UPDATE role_loadout_slot SET slot_key = ?, updated_at_utc = ? WHERE slot_id = ?", (PRIMARY_LOADOUT_SLOT_KEY, now, replacement["slot_id"]))
            connection.execute("UPDATE loadout_plan SET is_active = 0 WHERE slot_id = ?", (slot_id,))
            if replacement["current_plan_id"] is not None:
                connection.execute("UPDATE loadout_plan SET is_active = 1 WHERE plan_id = ?", (replacement["current_plan_id"],))
        connection.execute("UPDATE role_loadout_slot SET is_archived = 1, updated_at_utc = ? WHERE slot_id = ?", (now, slot_id))
