# 管理已保存配装方案的 SQLite 写入、冲突修复与原子替换。
"""Write-side mixin extracted from the loadout plan DAO."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from src.domain.loadout_plan_scores import exact_assignment_score_total
from src.services.virtual_equipment_service import (
    is_virtual_equipment_assignment,
    make_virtual_equipment_assignment,
)

from .protocols import UserDataDaoMixinHost
from .user_data_support import (
    UserDataError,
    UserDataValidationError,
    _decoded,
    _integer,
    _json,
    _plain_object,
    _utc_now,
)

class LoadoutPlanWriteDaoMixin(UserDataDaoMixinHost):
    def save_loadout_plan(
        self,
        *,
        name: str,
        character_id: int,
        assignments: Sequence[Mapping[str, Any]],
        source_snapshot_id: int | None = None,
        status: str = "draft",
        score: float | None = None,
        payload: Mapping[str, Any] | None = None,
        is_active: bool = False,
        slot_id: int | None = None,
    ) -> int:
        plan_name = str(name).strip()
        if not plan_name:
            raise UserDataValidationError("装配方案名称不能为空")
        raw_character_id = _integer(character_id, "character_id", minimum=1)
        raw_status = str(status).strip()
        if not raw_status:
            raise UserDataValidationError("装配方案状态不能为空")
        normalized: list[tuple[int, int, str, dict[str, Any]]] = []
        seen: set[tuple[int, int]] = set()
        for ordinal, raw_assignment in enumerate(assignments):
            assignment = _plain_object(raw_assignment, f"assignments[{ordinal}]")
            serial = _integer(assignment.get("uid_serial"), "assignment uid_serial", minimum=0)
            slot = _integer(assignment.get("uid_slot"), "assignment uid_slot", minimum=0)
            kind = assignment.get("kind")
            if kind not in ("module", "core"):
                raise UserDataValidationError("装配项 kind 必须是 module 或 core")
            if (serial, slot) in seen:
                raise UserDataValidationError("同一装配方案不能重复使用相同 UID")
            seen.add((serial, slot))
            for coordinate in ("target_row", "target_column"):
                value = assignment.get(coordinate)
                if value is not None and _integer(value, coordinate) not in range(1, 6):
                    raise UserDataValidationError(f"{coordinate} 必须在 1 到 5 之间")
            normalized.append((serial, slot, kind, assignment))
        if is_active:
            if source_snapshot_id is None:
                raise UserDataValidationError("激活装配方案必须记录来源背包快照")
            return self.replace_active_loadout_plans([{
                "name": plan_name,
                "character_id": raw_character_id,
                "source_snapshot_id": source_snapshot_id,
                "status": raw_status,
                "score": score,
                "payload": dict(payload or {}),
                "slot_id": slot_id,
                "assignments": [
                    assignment for _serial, _slot, _kind, assignment in normalized
                ],
            }])[0]
        role_name = str((payload or {}).get("source_role_name") or "").strip()
        resolved_slot_id = (
            self._resolve_loadout_slot_id(
                raw_character_id,
                slot_id,
                default_name=role_name,
            )
            if slot_id is not None
            else None
        )
        connection = self._db()
        now = _utc_now()
        owns_transaction = not connection.in_transaction
        try:
            if owns_transaction:
                connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT INTO loadout_plan(
                    name, character_id, source_snapshot_id, status, score,
                    payload_json, is_active, slot_id, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_name, raw_character_id, source_snapshot_id, raw_status,
                    float(score) if score is not None else None,
                    _json(dict(payload or {})), int(is_active), resolved_slot_id, now, now,
                ),
            )
            if cursor.lastrowid is None:
                raise UserDataError("创建配装方案后未返回 plan_id")
            plan_id = int(cursor.lastrowid)
            for ordinal, (serial, slot, kind, assignment) in enumerate(normalized):
                connection.execute(
                    """
                    INSERT INTO loadout_plan_item(
                        plan_id, ordinal, uid_serial, uid_slot, kind,
                        target_row, target_column, rotation, raw_assignment_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan_id, ordinal, serial, slot, kind,
                        assignment.get("target_row"), assignment.get("target_column"),
                        assignment.get("rotation"), _json(assignment),
                    ),
                )
            if resolved_slot_id is not None:
                connection.execute(
                    """
                    UPDATE role_loadout_slot
                    SET current_plan_id = ?, updated_at_utc = ?
                    WHERE slot_id = ?
                    """,
                    (plan_id, now, resolved_slot_id),
                )
            if owns_transaction:
                connection.commit()
            return plan_id
        except sqlite3.Error as exc:
            if owns_transaction:
                connection.rollback()
            raise UserDataError("无法保存装配方案") from exc

    def _repair_active_loadout_plan_conflicts_in_transaction(
        self,
        *,
        now: str,
        preferred_plan_ids: set[int] | None = None,
    ) -> int:
        """Keep one owner per native UID while preserving non-conflicting items."""

        connection = self._db()
        preferred = preferred_plan_ids or set()
        active_plans = [
            plan for plan in self.list_loadout_plans() if plan["is_active"]
        ]
        self.assert_allocation_lock_invariants()
        active_plans.sort(
            key=lambda plan: (
                bool(plan.get("allocation_locked")),
                int(plan["plan_id"]) in preferred,
                str(plan.get("updated_at_utc") or ""),
                int(plan["plan_id"]),
            ),
            reverse=True,
        )
        uid_owner: dict[tuple[int, int], int] = {}
        removed_by_plan: dict[int, set[tuple[int, int]]] = {}
        plans_by_id = {
            int(plan["plan_id"]): plan for plan in active_plans
        }
        for plan in active_plans:
            plan_id = int(plan["plan_id"])
            for item in plan["assignments"]:
                uid = (int(item["uid_slot"]), int(item["uid_serial"]))
                if uid[0] == 0:
                    continue
                owner = uid_owner.setdefault(uid, plan_id)
                if owner != plan_id:
                    removed_by_plan.setdefault(plan_id, set()).add(uid)

        for plan_id, removed_uids in removed_by_plan.items():
            plan = plans_by_id[plan_id]
            connection.execute(
                """
                UPDATE loadout_plan
                SET is_active = 0, updated_at_utc = ?
                WHERE plan_id = ?
                """,
                (now, plan_id),
            )
            residual_assignments: list[dict[str, Any]] = []
            for item in plan["assignments"]:
                uid = (int(item["uid_slot"]), int(item["uid_serial"]))
                if uid in removed_uids:
                    continue
                assignment = dict(item["raw_assignment"])
                assignment.update({
                    "uid_slot": uid[0],
                    "uid_serial": uid[1],
                    "kind": item["kind"],
                    "target_row": item.get("target_row"),
                    "target_column": item.get("target_column"),
                    "rotation": item.get("rotation"),
                })
                residual_assignments.append(assignment)
            if not residual_assignments:
                continue
            residual_payload = dict(plan.get("payload") or {})
            previous_source = residual_payload.get("source")
            residual_payload["source"] = "active_plan_conflict_repair"
            residual_payload["active_plan_conflict_repair"] = {
                "previous_plan_id": plan_id,
                "previous_source": previous_source,
                "removed_uids": [
                    {"uid_slot": slot, "uid_serial": serial}
                    for slot, serial in sorted(removed_uids)
                ],
            }
            cursor = connection.execute(
                """
                INSERT INTO loadout_plan(
                    name, character_id, source_snapshot_id, status, score,
                    payload_json, is_active, slot_id, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, NULL, ?, 1, ?, ?, ?)
                """,
                (
                    plan["name"],
                    int(plan["character_id"]),
                    plan.get("source_snapshot_id"),
                    (
                        "ready"
                        if any(
                            item.get("kind") == "module"
                            for item in residual_assignments
                        )
                        else "incomplete"
                    ),
                    _json(residual_payload),
                    plan.get("slot_id"),
                    now,
                    now,
                ),
            )
            if cursor.lastrowid is None:
                raise UserDataError("创建残留配装方案后未返回 plan_id")
            residual_plan_id = int(cursor.lastrowid)
            if plan.get("slot_id") is not None:
                connection.execute(
                    """
                    UPDATE role_loadout_slot
                    SET current_plan_id = ?, updated_at_utc = ?
                    WHERE slot_id = ?
                    """,
                    (residual_plan_id, now, int(plan["slot_id"])),
                )
            for ordinal, assignment in enumerate(residual_assignments):
                connection.execute(
                    """
                    INSERT INTO loadout_plan_item(
                        plan_id, ordinal, uid_serial, uid_slot, kind,
                        target_row, target_column, rotation, raw_assignment_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        residual_plan_id,
                        ordinal,
                        assignment["uid_serial"],
                        assignment["uid_slot"],
                        assignment["kind"],
                        assignment.get("target_row"),
                        assignment.get("target_column"),
                        assignment.get("rotation"),
                        _json(assignment),
                    ),
                )
        return len(removed_by_plan)

    def repair_active_loadout_plan_conflicts(self) -> int:
        """Atomically repair historical active plans that share native UIDs."""

        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            repaired = self._repair_active_loadout_plan_conflicts_in_transaction(
                now=_utc_now()
            )
            duplicate = connection.execute(
                """
                SELECT 1
                FROM loadout_plan_item AS item
                JOIN loadout_plan AS plan USING(plan_id)
                WHERE plan.is_active = 1
                  AND item.uid_slot > 0
                GROUP BY item.uid_slot, item.uid_serial
                HAVING COUNT(*) > 1
                LIMIT 1
                """
            ).fetchone()
            if duplicate is not None:
                raise UserDataValidationError(
                    "修复后仍存在被多个激活方案占用的装备"
                )
            connection.commit()
            return repaired
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法修复激活装配方案的装备占用冲突") from exc
        except BaseException:
            connection.rollback()
            raise

    def replace_active_loadout_plans(
        self,
        plans: Sequence[Mapping[str, Any]],
    ) -> tuple[int, ...]:
        """原子覆盖多个角色方案，并为被借装备的原槽位补入虚拟占位。"""

        normalized_plans: list[dict[str, Any]] = []
        target_characters: set[int] = set()
        claimed_uids: dict[tuple[int, int], int] = {}
        for plan_index, raw_plan in enumerate(plans):
            plan = _plain_object(raw_plan, f"plans[{plan_index}]")
            name = str(plan.get("name") or "").strip()
            if not name:
                raise UserDataValidationError("装配方案名称不能为空")
            character_id = _integer(
                plan.get("character_id"), "character_id", minimum=1
            )
            if character_id in target_characters:
                raise UserDataValidationError("批量保存中不能重复覆盖同一角色")
            target_characters.add(character_id)
            snapshot_id = _integer(
                plan.get("source_snapshot_id"), "source_snapshot_id", minimum=1
            )
            status = str(plan.get("status") or "ready").strip()
            if not status:
                raise UserDataValidationError("装配方案状态不能为空")
            normalized_assignments: list[dict[str, Any]] = []
            role_uids: set[tuple[int, int]] = set()
            for ordinal, raw_assignment in enumerate(plan.get("assignments") or ()):
                assignment = _plain_object(
                    raw_assignment, f"plans[{plan_index}].assignments[{ordinal}]"
                )
                serial = _integer(
                    assignment.get("uid_serial"), "assignment uid_serial", minimum=0
                )
                slot = _integer(
                    assignment.get("uid_slot"), "assignment uid_slot", minimum=0
                )
                kind = assignment.get("kind")
                if kind not in ("module", "core"):
                    raise UserDataValidationError(
                        "装配项 kind 必须是 module 或 core"
                    )
                uid = (slot, serial)
                virtual = is_virtual_equipment_assignment(assignment)
                if virtual:
                    if slot != 0 or serial <= 0:
                        raise UserDataValidationError(
                            "虚拟占位装备必须使用 slot=0 的正整数虚拟 UID"
                        )
                elif slot <= 0 or serial <= 0:
                    raise UserDataValidationError(
                        "真实装配项必须使用正整数原生 UID"
                    )
                if uid in role_uids:
                    raise UserDataValidationError(
                        "同一装配方案不能重复使用相同 UID"
                    )
                if not virtual and uid in claimed_uids:
                    raise UserDataValidationError(
                        f"批量保存中的装备 UID {uid} 同时分配给多个角色"
                    )
                role_uids.add(uid)
                if not virtual:
                    claimed_uids[uid] = character_id
                for coordinate in ("target_row", "target_column"):
                    value = assignment.get(coordinate)
                    if value is not None and _integer(value, coordinate) not in range(1, 6):
                        raise UserDataValidationError(
                            f"{coordinate} 必须在 1 到 5 之间"
                        )
                normalized_assignments.append(assignment)
            if not normalized_assignments:
                raise UserDataValidationError("每个激活方案至少需要一件装备")
            normalized_plans.append({
                "name": name,
                "character_id": character_id,
                "slot_id": plan.get("slot_id"),
                "source_snapshot_id": snapshot_id,
                "status": status,
                "score": (
                    float(plan["score"]) if plan.get("score") is not None else None
                ),
                "payload": dict(plan.get("payload") or {}),
                "assignments": normalized_assignments,
            })
        if not normalized_plans:
            raise UserDataValidationError("没有可保存的装配方案")

        connection = self._db()
        now = _utc_now()

        def insert_plan(plan: Mapping[str, Any], *, is_active: bool = True) -> int:
            cursor = connection.execute(
                """
                INSERT INTO loadout_plan(
                    name, character_id, source_snapshot_id, status, score,
                    payload_json, is_active, slot_id, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan["name"], plan["character_id"], plan["source_snapshot_id"],
                    plan["status"], plan.get("score"),
                    _json(dict(plan.get("payload") or {})), int(is_active),
                    plan.get("slot_id"), now, now,
                ),
            )
            if cursor.lastrowid is None:
                raise UserDataError("创建配装方案后未返回 plan_id")
            plan_id = int(cursor.lastrowid)
            for ordinal, assignment in enumerate(plan.get("assignments") or ()):
                connection.execute(
                    """
                    INSERT INTO loadout_plan_item(
                        plan_id, ordinal, uid_serial, uid_slot, kind,
                        target_row, target_column, rotation, raw_assignment_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan_id, ordinal, assignment["uid_serial"],
                        assignment["uid_slot"], assignment["kind"],
                        assignment.get("target_row"),
                        assignment.get("target_column"),
                        assignment.get("rotation"), _json(dict(assignment)),
                    ),
                )
            return plan_id

        try:
            connection.execute("BEGIN IMMEDIATE")
            for plan in normalized_plans:
                plan["slot_id"] = self._resolve_loadout_slot_id(
                    int(plan["character_id"]),
                    plan.get("slot_id"),
                    default_name=str(
                        (plan.get("payload") or {}).get("source_role_name") or ""
                    ).strip(),
                )
                slot = connection.execute(
                    "SELECT slot_key FROM role_loadout_slot WHERE slot_id = ?",
                    (plan["slot_id"],),
                ).fetchone()
                if slot is None or str(slot["slot_key"]) != "primary":
                    raise UserDataValidationError(
                        "活动配装兼容入口只能覆盖主力槽位；备用槽位请使用槽位保存接口"
                    )
            self.assert_active_allocation_locks_preserved(
                target_characters=target_characters,
                claimed_uids=set(claimed_uids),
            )
            def inventory_assignment_item(row: sqlite3.Row) -> dict[str, Any]:
                item = dict(row)
                if "names_json" in item:
                    item["names"] = _decoded(item.pop("names_json"), {})
                if "suit_names_json" in item:
                    item["suit_names"] = _decoded(
                        item.pop("suit_names_json"), {}
                    )
                return item

            inventory_by_snapshot: dict[
                int, dict[tuple[int, int], dict[str, Any]]
            ] = {}
            for snapshot_id in {
                int(plan["source_snapshot_id"]) for plan in normalized_plans
            }:
                snapshot = connection.execute(
                    """
                    SELECT complete, declared_item_count, stored_item_count
                    FROM inventory_snapshot WHERE snapshot_id = ?
                    """,
                    (snapshot_id,),
                ).fetchone()
                if (
                    snapshot is None
                    or not bool(snapshot["complete"])
                    or int(snapshot["declared_item_count"])
                    != int(snapshot["stored_item_count"])
                ):
                    raise UserDataValidationError(
                        f"背包快照不可用于保存：{snapshot_id}"
                    )
                inventory_by_snapshot[snapshot_id] = {
                    (
                        int(row["uid_slot"]),
                        int(row["uid_serial"]),
                    ): inventory_assignment_item(row)
                    for row in connection.execute(
                        """
                        SELECT uid_slot, uid_serial, kind, item_id, suit_id,
                               geometry, grid_count, quality,
                               names_json, suit_names_json
                        FROM inventory_item WHERE snapshot_id = ?
                        """,
                        (snapshot_id,),
                    )
                }
            for plan in normalized_plans:
                inventory = inventory_by_snapshot[int(plan["source_snapshot_id"])]
                for assignment in plan["assignments"]:
                    uid = (
                        int(assignment["uid_slot"]),
                        int(assignment["uid_serial"]),
                    )
                    if is_virtual_equipment_assignment(assignment):
                        continue
                    if (inventory.get(uid) or {}).get("kind") != assignment["kind"]:
                        raise UserDataValidationError(
                            f"装备 UID {uid} 不在方案固定的背包快照中"
                        )
            affected_plans = [
                plan
                for plan in self.list_loadout_plans()
                if plan["is_active"] and (
                    int(plan["character_id"]) in target_characters
                    or any(
                        (int(item["uid_slot"]), int(item["uid_serial"]))
                        in claimed_uids
                        for item in plan["assignments"]
                    )
                )
            ]
            for plan in affected_plans:
                connection.execute(
                    """
                    UPDATE loadout_plan
                    SET is_active = 0, updated_at_utc = ?
                    WHERE plan_id = ?
                    """,
                    (now, int(plan["plan_id"])),
                )
                if int(plan["character_id"]) in target_characters:
                    continue
                removed_uids = [
                    (int(item["uid_slot"]), int(item["uid_serial"]))
                    for item in plan["assignments"]
                    if (
                        int(item["uid_slot"]), int(item["uid_serial"])
                    ) in claimed_uids
                ]
                source_snapshot_id = int(plan.get("source_snapshot_id") or 0)
                source_inventory = inventory_by_snapshot.get(
                    source_snapshot_id, {}
                )
                if source_snapshot_id > 0 and not source_inventory:
                    source_inventory = {
                        (
                            int(row["uid_slot"]),
                            int(row["uid_serial"]),
                        ): inventory_assignment_item(row)
                        for row in connection.execute(
                            """
                            SELECT uid_slot, uid_serial, kind, item_id, suit_id,
                                   geometry, grid_count, quality,
                                   names_json, suit_names_json
                            FROM inventory_item WHERE snapshot_id = ?
                            """,
                            (source_snapshot_id,),
                        )
                    }
                residual_assignments = []
                virtual_changes: list[tuple[tuple[int, int], dict[str, Any]]] = []
                for ordinal, item in enumerate(plan["assignments"]):
                    uid = (
                        int(item["uid_slot"]),
                        int(item["uid_serial"]),
                    )
                    assignment = dict(item["raw_assignment"])
                    assignment.update({
                        "uid_slot": uid[0],
                        "uid_serial": uid[1],
                        "kind": item["kind"],
                        "target_row": item.get("target_row"),
                        "target_column": item.get("target_column"),
                        "rotation": item.get("rotation"),
                    })
                    if uid in claimed_uids:
                        assignment = make_virtual_equipment_assignment(
                            assignment,
                            inventory_item=source_inventory.get(uid),
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
                    previous_key = (
                        f"nte-{assignment['kind']}-{uid[0]}-{uid[1]}"
                    )
                    previous_score = assignment_scores.pop(previous_key, None)
                    if previous_score is None:
                        known_removed_scores = False
                    else:
                        removed_score += float(previous_score)
                    virtual_key = (
                        f"nte-{assignment['kind']}-0-{assignment['uid_serial']}"
                    )
                    assignment_scores[virtual_key] = 0.0
                previous_score = plan.get("score")
                exact_residual_score = exact_assignment_score_total(
                    residual_assignments,
                    assignment_scores,
                )
                if exact_residual_score is not None:
                    residual_score: float | None = exact_residual_score
                elif previous_score is not None and known_removed_scores:
                    residual_score = max(0.0, float(previous_score) - removed_score)
                else:
                    residual_score = float(previous_score) if previous_score is not None else None
                previous_source = residual_payload.get("source")
                residual_payload["source"] = "active_plan_overlay"
                residual_payload["active_plan_overlay"] = {
                    "previous_plan_id": int(plan["plan_id"]),
                    "previous_source": previous_source,
                    "removed_uids": [
                        {"uid_slot": slot, "uid_serial": serial}
                        for slot, serial in removed_uids
                    ],
                    "replaced_by_character_ids": sorted(target_characters),
                }
                # The previous owner must see both the empty placeholder and a
                # normal saved-plan change record.  This keeps its card eligible
                # for the same replacement optimizer as any other slot.
                virtual_display_uids = [
                    f"nte-{assignment['kind']}-0-{assignment['uid_serial']}"
                    for _removed_uid, assignment in virtual_changes
                ]
                removed_display_uids = [
                    f"nte-{next(item['kind'] for item in plan['assignments'] if (int(item['uid_slot']), int(item['uid_serial'])) == uid)}-{uid[0]}-{uid[1]}"
                    for uid, _assignment in virtual_changes
                ]
                residual_payload["changed_uids"] = virtual_display_uids
                residual_payload["last_diff"] = {
                    "changed": bool(virtual_display_uids),
                    "added_uids": virtual_display_uids,
                    "added": [
                        {"uid": display_uid, "is_changed": True}
                        for display_uid in virtual_display_uids
                    ],
                    "removed": [
                        {"uid": display_uid}
                        for display_uid in removed_display_uids
                    ],
                }
                if assignment_scores:
                    residual_payload["assignment_scores"] = assignment_scores
                insert_plan({
                    "name": plan["name"],
                    "character_id": int(plan["character_id"]),
                    "slot_id": plan.get("slot_id"),
                    "source_snapshot_id": plan.get("source_snapshot_id"),
                    "status": "incomplete",
                    "score": residual_score,
                    "payload": residual_payload,
                    "assignments": residual_assignments,
                })

            saved_plan_ids = tuple(insert_plan(plan) for plan in normalized_plans)
            for plan_id, plan in zip(saved_plan_ids, normalized_plans, strict=True):
                connection.execute(
                    """
                    UPDATE role_loadout_slot
                    SET current_plan_id = ?, updated_at_utc = ?
                    WHERE slot_id = ?
                    """,
                    (plan_id, now, plan["slot_id"]),
                )
            self._repair_active_loadout_plan_conflicts_in_transaction(
                now=now,
                preferred_plan_ids=set(saved_plan_ids),
            )
            duplicate = connection.execute(
                """
                SELECT item.uid_slot, item.uid_serial, COUNT(*) AS use_count
                FROM loadout_plan_item AS item
                JOIN loadout_plan AS plan USING(plan_id)
                WHERE plan.is_active = 1
                  AND item.uid_slot > 0
                GROUP BY item.uid_slot, item.uid_serial
                HAVING COUNT(*) > 1
                LIMIT 1
                """
            ).fetchone()
            if duplicate is not None:
                raise UserDataValidationError(
                    "保存后仍存在被多个激活方案占用的装备 UID："
                    f"({duplicate['uid_slot']}, {duplicate['uid_serial']})"
                )
            connection.commit()
            return saved_plan_ids
        except (sqlite3.Error, UserDataValidationError) as exc:
            connection.rollback()
            if isinstance(exc, UserDataValidationError):
                raise
            raise UserDataError("无法原子覆盖激活装配方案") from exc
        except BaseException:
            connection.rollback()
            raise
