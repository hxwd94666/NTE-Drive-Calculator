# 管理已保存配装方案的 SQLite 访问方法。
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .user_data_support import (
    ALLOCATION_STRATEGIES,
    DEFAULT_SCHEMA_PATH,
    DEFAULT_SNAPSHOT_RETENTION_COUNT,
    SNAPSHOT_SOURCES,
    SUIT_REQUIREMENT_MODES,
    SYNC_METHODS,
    USER_MIGRATIONS,
    SCHEMA_VERSION,
    UserDataError,
    UserDataValidationError,
    _decoded,
    _integer,
    _json,
    _mark_duplicate_modules,
    _plain_object,
    _utc_now,
)

from src.services.virtual_equipment_service import (
    is_virtual_equipment_assignment,
    make_virtual_equipment_assignment,
)


class LoadoutPlanDaoMixin:
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
                "assignments": [
                    assignment for _serial, _slot, _kind, assignment in normalized
                ],
            }])[0]
        connection = self._db()
        now = _utc_now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if is_active:
                connection.execute(
                    "UPDATE loadout_plan SET is_active = 0, updated_at_utc = ? WHERE character_id = ?",
                    (now, raw_character_id),
                )
            cursor = connection.execute(
                """
                INSERT INTO loadout_plan(
                    name, character_id, source_snapshot_id, status, score,
                    payload_json, is_active, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_name, raw_character_id, source_snapshot_id, raw_status,
                    float(score) if score is not None else None,
                    _json(dict(payload or {})), int(is_active), now, now,
                ),
            )
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
            connection.commit()
            return plan_id
        except sqlite3.Error as exc:
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
        active_plans.sort(
            key=lambda plan: (
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
                    payload_json, is_active, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, NULL, ?, 1, ?, ?)
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
                    now,
                    now,
                ),
            )
            residual_plan_id = int(cursor.lastrowid)
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
                    payload_json, is_active, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan["name"], plan["character_id"], plan["source_snapshot_id"],
                    plan["status"], plan.get("score"),
                    _json(dict(plan.get("payload") or {})), int(is_active), now, now,
                ),
            )
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
                insert_plan({
                    "name": plan["name"],
                    "character_id": int(plan["character_id"]),
                    "source_snapshot_id": plan.get("source_snapshot_id"),
                    "status": "incomplete",
                    "score": None,
                    "payload": residual_payload,
                    "assignments": residual_assignments,
                })

            saved_plan_ids = tuple(insert_plan(plan) for plan in normalized_plans)
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

    def list_loadout_plans(self, character_id: int | None = None) -> list[dict[str, Any]]:
        where = "" if character_id is None else "WHERE character_id = ?"
        parameters = () if character_id is None else (_integer(character_id, "character_id", minimum=1),)
        rows = self._rows(
            f"""
            SELECT plan_id, name, character_id, source_snapshot_id, status,
                   score, payload_json, is_active, created_at_utc, updated_at_utc
            FROM loadout_plan {where}
            ORDER BY updated_at_utc DESC, plan_id DESC
            """,
            parameters,
        )
        for row in rows:
            row["is_active"] = bool(row["is_active"])
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
                and plan["payload"].get("schema") == "allocation-official-snapshot-v1"
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
                and payload.get("schema") == "allocation-official-snapshot-v1"
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
        """从当前 UI 和新装配入口移除方案，但保留历史记录和任务审计。"""

        raw_plan_id = _integer(plan_id, "plan_id", minimum=1)
        cursor = self._db().execute(
            "UPDATE loadout_plan SET is_active = 0, updated_at_utc = ? WHERE plan_id = ? AND is_active = 1",
            (_utc_now(), raw_plan_id),
        )
        self._db().commit()
        return cursor.rowcount > 0

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "database_path": str(self.database_path),
            "profile": self.profile(),
            "sync_settings": self.get_sync_settings(),
            "inventory": self.current_inventory_summary(),
            "snapshot_count": self._one(
                "SELECT COUNT(*) AS count FROM inventory_snapshot"
            )["count"],
            "loadout_plan_count": self._one(
                "SELECT COUNT(*) AS count FROM loadout_plan"
            )["count"],
        }

    def integrity_check(self) -> dict[str, Any]:
        quick_check = [row["quick_check"] for row in self._rows("PRAGMA quick_check")]
        foreign_key_errors = self._rows("PRAGMA foreign_key_check")
        return {
            "ok": quick_check == ["ok"] and not foreign_key_errors,
            "quick_check": quick_check,
            "foreign_key_errors": foreign_key_errors,
        }

