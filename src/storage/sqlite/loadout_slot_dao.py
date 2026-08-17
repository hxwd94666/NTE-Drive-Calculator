# 管理角色多配装槽位及其当前方案指针。
"""SQLite access methods for named per-character loadout slots."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from src.domain.loadout_plan_scores import exact_assignment_score_total
from src.services.loadout_equipment_identity import source_snapshots_share_equipment_uids
from src.services.virtual_equipment_service import (
    is_virtual_equipment_assignment,
    make_virtual_equipment_assignment,
)

from .protocols import UserDataDaoMixinHost
from .user_data_support import UserDataError, UserDataValidationError, _integer, _utc_now


PRIMARY_LOADOUT_SLOT_KEY = "primary"
MAX_VISIBLE_LOADOUT_SLOTS = 3


class LoadoutSlotDaoMixin(UserDataDaoMixinHost):
    """Own named role slots while legacy active-plan callers remain compatible."""

    def list_loadout_slots(
        self,
        character_id: int,
        *,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        raw_character_id = _integer(character_id, "character_id", minimum=1)
        where = "character_id = ?"
        parameters: list[Any] = [raw_character_id]
        if not include_archived:
            where += " AND is_archived = 0"
        rows = self._rows(
            f"""
            SELECT slot_id, character_id, slot_key, slot_name, sort_order,
                   current_plan_id, is_archived, created_at_utc, updated_at_utc
            FROM role_loadout_slot
            WHERE {where}
            ORDER BY sort_order, slot_id
            """,
            parameters,
        )
        for row in rows:
            row["is_archived"] = bool(row["is_archived"])
            current_plan_id = row.get("current_plan_id")
            row["current_plan"] = (
                self.get_loadout_plan(int(current_plan_id))
                if current_plan_id is not None
                else None
            )
        return rows

    def list_visible_loadout_slots(self) -> list[dict[str, Any]]:
        """List every non-archived slot, including slots without a plan."""

        return self._rows(
            """
            SELECT slot_id, character_id, slot_key, slot_name, sort_order,
                   current_plan_id, is_archived, created_at_utc, updated_at_utc
            FROM role_loadout_slot
            WHERE is_archived = 0
            ORDER BY character_id, sort_order, slot_id
            """
        )

    def list_current_loadout_equipment_owners(self) -> list[dict[str, Any]]:
        """Return physical equipment referenced by every visible current slot."""

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

    def list_current_loadout_slot_plans(
        self,
        *,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        """Return one current saved plan projection per visible role slot."""

        where = "current_plan_id IS NOT NULL"
        if not include_archived:
            where += " AND is_archived = 0"
        slots = self._rows(
            f"""
            SELECT slot_id, character_id, slot_key, slot_name, sort_order,
                   current_plan_id, is_archived, created_at_utc, updated_at_utc
            FROM role_loadout_slot
            WHERE {where}
            ORDER BY character_id, sort_order, slot_id
            """,
        )
        results: list[dict[str, Any]] = []
        for slot in slots:
            plan = self.get_loadout_plan(int(slot["current_plan_id"]))
            if plan is None:
                continue
            slot["is_archived"] = bool(slot["is_archived"])
            results.append({"slot": slot, "plan": plan})
        return results

    def get_loadout_slot(self, slot_id: int) -> dict[str, Any] | None:
        raw_slot_id = _integer(slot_id, "slot_id", minimum=1)
        row = self._one(
            """
            SELECT slot_id, character_id, slot_key, slot_name, sort_order,
                   current_plan_id, is_archived, created_at_utc, updated_at_utc
            FROM role_loadout_slot WHERE slot_id = ?
            """,
            (raw_slot_id,),
        )
        if row is None:
            return None
        row["is_archived"] = bool(row["is_archived"])
        current_plan_id = row.get("current_plan_id")
        row["current_plan"] = (
            self.get_loadout_plan(int(current_plan_id))
            if current_plan_id is not None
            else None
        )
        return row

    def create_loadout_slot(
        self,
        character_id: int,
        name: str,
        *,
        slot_key: str | None = None,
    ) -> int:
        raw_character_id = _integer(character_id, "character_id", minimum=1)
        slot_name = self._normalize_slot_name(name)
        key = self._normalize_slot_key(slot_key or self._next_slot_key(raw_character_id))
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            visible = connection.execute(
                "SELECT COUNT(*) FROM role_loadout_slot WHERE character_id = ? AND is_archived = 0",
                (raw_character_id,),
            ).fetchone()
            if int(visible[0] if visible is not None else 0) >= MAX_VISIBLE_LOADOUT_SLOTS:
                raise UserDataValidationError(
                    f"每个角色最多创建 {MAX_VISIBLE_LOADOUT_SLOTS} 个配装槽位"
                )
            order_row = connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM role_loadout_slot WHERE character_id = ?",
                (raw_character_id,),
            ).fetchone()
            now = _utc_now()
            cursor = connection.execute(
                """
                INSERT INTO role_loadout_slot(
                    character_id, slot_key, slot_name, sort_order,
                    current_plan_id, is_archived, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, NULL, 0, ?, ?)
                """,
                (raw_character_id, key, slot_name, int(order_row[0]), now, now),
            )
            if cursor.lastrowid is None:
                raise UserDataError("创建配装槽位后未返回 slot_id")
            connection.commit()
            return int(cursor.lastrowid)
        except (sqlite3.Error, UserDataValidationError) as exc:
            connection.rollback()
            if isinstance(exc, UserDataValidationError):
                raise
            raise UserDataError("无法创建配装槽位") from exc
        except BaseException:
            connection.rollback()
            raise

    def rename_loadout_slot(self, slot_id: int, name: str) -> bool:
        raw_slot_id = _integer(slot_id, "slot_id", minimum=1)
        slot_name = self._normalize_slot_name(name)
        cursor = self._db().execute(
            """
            UPDATE role_loadout_slot
            SET slot_name = ?, updated_at_utc = ?
            WHERE slot_id = ? AND is_archived = 0
            """,
            (slot_name, _utc_now(), raw_slot_id),
        )
        self._db().commit()
        return cursor.rowcount == 1

    def archive_loadout_slot(self, slot_id: int) -> bool:
        raw_slot_id = _integer(slot_id, "slot_id", minimum=1)
        slot = self.get_loadout_slot(raw_slot_id)
        if slot is None or slot["is_archived"]:
            return False
        current_plan = slot.get("current_plan") or {}
        if current_plan.get("allocation_locked"):
            raise UserDataValidationError("锁定方案所在槽位不能归档；请先解除锁定")
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = _utc_now()
            if slot["slot_key"] == PRIMARY_LOADOUT_SLOT_KEY:
                replacement = connection.execute(
                    """
                    SELECT slot_id, current_plan_id
                    FROM role_loadout_slot
                    WHERE character_id = ? AND is_archived = 0 AND slot_id != ?
                    ORDER BY sort_order, slot_id
                    LIMIT 1
                    """,
                    (int(slot["character_id"]), raw_slot_id),
                ).fetchone()
                if replacement is None:
                    raise UserDataValidationError("至少保留一个配装槽位后才能删除默认槽位")
                connection.execute(
                    """
                    UPDATE role_loadout_slot
                    SET slot_key = ?, updated_at_utc = ?
                    WHERE slot_id = ?
                    """,
                    (f"archived-{raw_slot_id}", now, raw_slot_id),
                )
                connection.execute(
                    """
                    UPDATE role_loadout_slot
                    SET slot_key = ?, updated_at_utc = ?
                    WHERE slot_id = ?
                    """,
                    (PRIMARY_LOADOUT_SLOT_KEY, now, int(replacement["slot_id"])),
                )
                connection.execute(
                    "UPDATE loadout_plan SET is_active = 0 WHERE slot_id = ?",
                    (raw_slot_id,),
                )
                if replacement["current_plan_id"] is not None:
                    connection.execute(
                        "UPDATE loadout_plan SET is_active = 1 WHERE plan_id = ?",
                        (int(replacement["current_plan_id"]),),
                    )
            cursor = connection.execute(
                """
                UPDATE role_loadout_slot
                SET is_archived = 1, updated_at_utc = ?
                WHERE slot_id = ? AND is_archived = 0
                """,
                (now, raw_slot_id),
            )
            connection.commit()
            return cursor.rowcount == 1
        except (sqlite3.Error, UserDataValidationError):
            connection.rollback()
            raise
        except BaseException:
            connection.rollback()
            raise

    def save_plan_to_slot(
        self,
        slot_id: int,
        *,
        name: str,
        assignments: Sequence[Mapping[str, Any]],
        source_snapshot_id: int | None,
        status: str = "ready",
        score: float | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> int:
        slot = self.get_loadout_slot(slot_id)
        if slot is None or slot["is_archived"]:
            raise UserDataValidationError("配装槽位不存在或已归档")
        self.assert_loadout_slot_save_allowed(
            int(slot["slot_id"]), assignments, source_snapshot_id=source_snapshot_id
        )
        return self.save_loadout_plan(
            name=name,
            character_id=int(slot["character_id"]),
            assignments=assignments,
            source_snapshot_id=source_snapshot_id,
            status=status,
            score=score,
            payload=payload,
            is_active=False,
            slot_id=int(slot["slot_id"]),
        )

    def save_replacement_plan_to_slot(
        self,
        slot_id: int,
        *,
        name: str,
        assignments: Sequence[Mapping[str, Any]],
        source_snapshot_id: int | None,
        status: str = "ready",
        score: float | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> int:
        """Save one optimized slot and release compatible other-role owners.

        A replacement candidate can be borrowed from another role's current
        slot. Its former slot receives a same-position virtual placeholder in
        the same transaction, rather than retaining a duplicate physical item.
        Multiple slots of the same role are alternative loadouts, so they may
        retain references to the same physical item.
        Visual UID fields are local to their scan snapshot; only same visual
        snapshots (or two native nte-core snapshots) are considered the same
        item.
        """

        target_slot = self.get_loadout_slot(slot_id)
        if target_slot is None or target_slot["is_archived"]:
            raise UserDataValidationError("配装槽位不存在或已归档")
        if source_snapshot_id is None:
            raise UserDataValidationError("替换优化必须保留来源背包快照")
        target_snapshot_id = _integer(
            source_snapshot_id, "source_snapshot_id", minimum=1
        )
        self.assert_loadout_slot_save_allowed(
            int(slot_id), assignments, source_snapshot_id=target_snapshot_id
        )
        claimed_uids = {
            (int(item["uid_slot"]), int(item["uid_serial"]))
            for item in assignments
            if isinstance(item, Mapping)
            and not is_virtual_equipment_assignment(item)
            and int(item.get("uid_slot") or 0) > 0
            and int(item.get("uid_serial") or 0) > 0
        }
        if not claimed_uids:
            return self.save_plan_to_slot(
                int(slot_id), name=name, assignments=assignments,
                source_snapshot_id=target_snapshot_id, status=status,
                score=score, payload=payload,
            )

        target_summary = self.inventory_snapshot_summary(target_snapshot_id) or {}
        current_slots = self.list_current_loadout_slot_plans()
        owners: list[dict[str, Any]] = []
        for row in current_slots:
            owner_slot = row["slot"]
            owner_plan = row["plan"]
            if int(owner_slot["slot_id"]) == int(slot_id):
                continue
            if int(owner_slot["character_id"]) == int(target_slot["character_id"]):
                continue
            owner_snapshot_id = owner_plan.get("source_snapshot_id")
            if owner_snapshot_id is None:
                continue
            owner_summary = self.inventory_snapshot_summary(int(owner_snapshot_id)) or {}
            if not source_snapshots_share_equipment_uids(
                target_snapshot_id,
                target_summary.get("source"),
                int(owner_snapshot_id),
                owner_summary.get("source"),
            ):
                continue
            removed_uids = {
                (int(item["uid_slot"]), int(item["uid_serial"]))
                for item in owner_plan.get("assignments") or ()
                if int(item.get("uid_slot") or 0) > 0
                and (int(item["uid_slot"]), int(item["uid_serial"])) in claimed_uids
            }
            if not removed_uids:
                continue
            if owner_plan.get("allocation_locked"):
                raise UserDataValidationError(
                    "不能借用锁定槽位方案中的装备；请先解除原槽位锁定"
                )
            owners.append({"slot": owner_slot, "plan": owner_plan, "removed_uids": removed_uids})

        connection = self._db()
        if connection.in_transaction:
            raise UserDataError("替换优化保存不能嵌套事务")
        try:
            connection.execute("BEGIN IMMEDIATE")
            target_plan_id = self.save_loadout_plan(
                name=name,
                character_id=int(target_slot["character_id"]),
                assignments=assignments,
                source_snapshot_id=target_snapshot_id,
                status=status,
                score=score,
                payload=payload,
                is_active=False,
                slot_id=int(slot_id),
            )
            for owner in owners:
                self._save_released_owner_slot(
                    owner["slot"], owner["plan"], owner["removed_uids"],
                    received_by_slot_id=int(slot_id),
                    received_by_character_id=int(target_slot["character_id"]),
                )
            connection.commit()
            return target_plan_id
        except (sqlite3.Error, UserDataError, UserDataValidationError):
            connection.rollback()
            raise
        except BaseException:
            connection.rollback()
            raise

    def _save_released_owner_slot(
        self,
        slot: Mapping[str, Any],
        plan: Mapping[str, Any],
        removed_uids: set[tuple[int, int]],
        *,
        received_by_slot_id: int,
        received_by_character_id: int,
    ) -> None:
        """Persist the prior slot with virtual placeholders for transferred UIDs."""

        source_snapshot_id = _integer(
            plan.get("source_snapshot_id"), "source_snapshot_id", minimum=1
        )
        inventory = {
            (int(item["uid_slot"]), int(item["uid_serial"])): item
            for item in self.list_inventory_items(source_snapshot_id)
        }
        residual_assignments: list[dict[str, Any]] = []
        virtual_changes: list[tuple[tuple[int, int], dict[str, Any]]] = []
        for ordinal, item in enumerate(plan.get("assignments") or ()):
            uid = (int(item["uid_slot"]), int(item["uid_serial"]))
            assignment = dict(item.get("raw_assignment") or item)
            assignment.update({
                "uid_slot": uid[0], "uid_serial": uid[1], "kind": item["kind"],
                "target_row": item.get("target_row"),
                "target_column": item.get("target_column"),
                "rotation": item.get("rotation"),
            })
            if uid in removed_uids:
                assignment = make_virtual_equipment_assignment(
                    assignment,
                    inventory_item=inventory.get(uid),
                    character_id=int(plan["character_id"]),
                    ordinal=ordinal,
                )
                virtual_changes.append((uid, assignment))
            residual_assignments.append(assignment)
        residual_payload = dict(plan.get("payload") or {})
        assignment_scores = dict(residual_payload.get("assignment_scores") or {})
        removed_score = 0.0
        known_removed_scores = True
        for uid, assignment in virtual_changes:
            previous_key = f"nte-{assignment['kind']}-{uid[0]}-{uid[1]}"
            previous_score = assignment_scores.pop(previous_key, None)
            if previous_score is None:
                known_removed_scores = False
            else:
                removed_score += float(previous_score)
            assignment_scores[f"nte-{assignment['kind']}-0-{assignment['uid_serial']}"] = 0.0
        exact_score = exact_assignment_score_total(residual_assignments, assignment_scores)
        previous_score = plan.get("score")
        residual_score = (
            exact_score
            if exact_score is not None
            else max(0.0, float(previous_score) - removed_score)
            if previous_score is not None and known_removed_scores
            else float(previous_score) if previous_score is not None else None
        )
        virtual_display_uids = [
            f"nte-{assignment['kind']}-0-{assignment['uid_serial']}"
            for _uid, assignment in virtual_changes
        ]
        removed_display_uids = [
            f"nte-{assignment['kind']}-{uid[0]}-{uid[1]}"
            for uid, assignment in virtual_changes
        ]
        residual_payload.update({
            "source": "slot_equipment_transfer",
            "equipment_transfer": {
                "previous_plan_id": int(plan["plan_id"]),
                "received_by_slot_id": received_by_slot_id,
                "received_by_character_id": received_by_character_id,
                "removed_uids": [
                    {"uid_slot": uid_slot, "uid_serial": uid_serial}
                    for uid_slot, uid_serial in sorted(removed_uids)
                ],
            },
            "changed_uids": virtual_display_uids,
            "last_diff": {
                "changed": bool(virtual_display_uids),
                "added_uids": virtual_display_uids,
                "added": [
                    {"uid": display_uid, "is_changed": True}
                    for display_uid in virtual_display_uids
                ],
                "removed": [{"uid": display_uid} for display_uid in removed_display_uids],
            },
            "assignment_scores": assignment_scores,
        })
        self.save_loadout_plan(
            name=str(plan["name"]),
            character_id=int(plan["character_id"]),
            assignments=residual_assignments,
            source_snapshot_id=source_snapshot_id,
            status="incomplete",
            score=residual_score,
            payload=residual_payload,
            is_active=False,
            slot_id=int(slot["slot_id"]),
        )

    def save_plans_to_slots(
        self,
        plans: Sequence[Mapping[str, Any]],
    ) -> tuple[int, ...]:
        """Save current plans for distinct slots as one SQLite transaction."""

        if not plans:
            raise UserDataValidationError("至少需要保存一个槽位方案")
        slot_ids = [
            _integer(row.get("slot_id"), "slot_id", minimum=1)
            for row in plans
        ]
        if len(set(slot_ids)) != len(slot_ids):
            raise UserDataValidationError("同一次保存不能重复覆盖同一配装槽位")
        connection = self._db()
        if connection.in_transaction:
            raise UserDataError("配装槽位批量保存不能嵌套事务")
        try:
            connection.execute("BEGIN IMMEDIATE")
            plan_ids = tuple(
                self.save_plan_to_slot(
                    slot_id,
                    name=str(row.get("name") or ""),
                    assignments=row.get("assignments") or (),
                    source_snapshot_id=row.get("source_snapshot_id"),
                    status=str(row.get("status") or "ready"),
                    score=row.get("score"),
                    payload=row.get("payload") if isinstance(row.get("payload"), Mapping) else None,
                )
                for slot_id, row in zip(slot_ids, plans)
            )
            connection.commit()
            return plan_ids
        except (sqlite3.Error, UserDataError, UserDataValidationError):
            connection.rollback()
            raise
        except BaseException:
            connection.rollback()
            raise

    def is_current_loadout_slot_plan(self, plan_id: int) -> bool:
        raw_plan_id = _integer(plan_id, "plan_id", minimum=1)
        row = self._one(
            """
            SELECT 1
            FROM role_loadout_slot
            WHERE current_plan_id = ? AND is_archived = 0
            """,
            (raw_plan_id,),
        )
        return row is not None

    def _resolve_loadout_slot_id(
        self,
        character_id: int,
        slot_id: int | None,
        *,
        default_name: str | None = None,
    ) -> int:
        raw_character_id = _integer(character_id, "character_id", minimum=1)
        if slot_id is None:
            return self._ensure_primary_loadout_slot(raw_character_id, default_name=default_name)
        raw_slot_id = _integer(slot_id, "slot_id", minimum=1)
        slot = self._one(
            """
            SELECT slot_id, slot_key, slot_name FROM role_loadout_slot
            WHERE slot_id = ? AND character_id = ? AND is_archived = 0
            """,
            (raw_slot_id, raw_character_id),
        )
        if slot is None:
            raise UserDataValidationError("配装槽位不属于该角色或已经归档")
        self._rename_primary_default_slot_if_needed(slot, default_name)
        return raw_slot_id

    def _ensure_primary_loadout_slot(
        self,
        character_id: int,
        *,
        default_name: str | None = None,
    ) -> int:
        raw_character_id = _integer(character_id, "character_id", minimum=1)
        existing = self._one(
            """
            SELECT slot_id, slot_key, slot_name FROM role_loadout_slot
            WHERE character_id = ? AND slot_key = ?
            """,
            (raw_character_id, PRIMARY_LOADOUT_SLOT_KEY),
        )
        if existing is not None:
            self._rename_primary_default_slot_if_needed(existing, default_name)
            return int(existing["slot_id"])
        now = _utc_now()
        cursor = self._db().execute(
            """
            INSERT INTO role_loadout_slot(
                character_id, slot_key, slot_name, sort_order,
                current_plan_id, is_archived, created_at_utc, updated_at_utc
            ) VALUES (?, ?, '主力', 0, NULL, 0, ?, ?)
            """,
            (raw_character_id, PRIMARY_LOADOUT_SLOT_KEY, now, now),
        )
        if cursor.lastrowid is None:
            raise UserDataError("创建默认配装槽位后未返回 slot_id")
        slot = {
            "slot_id": int(cursor.lastrowid),
            "slot_key": PRIMARY_LOADOUT_SLOT_KEY,
            "slot_name": "主力",
        }
        self._rename_primary_default_slot_if_needed(slot, default_name)
        return int(cursor.lastrowid)

    def _rename_primary_default_slot_if_needed(
        self,
        slot: Mapping[str, Any],
        default_name: str | None,
    ) -> None:
        role_name = str(default_name or "").strip()
        if (
            str(slot.get("slot_key") or "") != PRIMARY_LOADOUT_SLOT_KEY
            or str(slot.get("slot_name") or "") != "主力"
            or not role_name
        ):
            return
        self._db().execute(
            "UPDATE role_loadout_slot SET slot_name = ?, updated_at_utc = ? WHERE slot_id = ?",
            (role_name, _utc_now(), int(slot["slot_id"])),
        )

    def _next_slot_key(self, character_id: int) -> str:
        row = self._one(
            "SELECT COUNT(*) AS count FROM role_loadout_slot WHERE character_id = ?",
            (character_id,),
        )
        return f"slot-{int((row or {}).get('count', 0)) + 1}"

    @staticmethod
    def _normalize_slot_name(name: str) -> str:
        value = str(name).strip()
        if not value or len(value) > 24:
            raise UserDataValidationError("配装槽位名称必须为 1 到 24 个字符")
        return value

    @staticmethod
    def _normalize_slot_key(value: str) -> str:
        key = str(value).strip().casefold()
        if not key or len(key) > 40 or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in key):
            raise UserDataValidationError("配装槽位键只能使用小写字母、数字、连字符或下划线")
        return key
