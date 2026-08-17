# 解析并校验具名配装槽位选择。
"""Resolve explicit multi-loadout selections for apply and presentation flows."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from src.storage.sqlite.user_data_support import UserDataValidationError


@dataclass(frozen=True)
class ResolvedLoadoutSlotSelection:
    """A current, non-archived named slot pinned to its saved plan."""

    slot_id: int
    character_id: int
    slot_key: str
    slot_name: str
    role_name: str
    plan_id: int
    source_snapshot_id: int | None
    plan: Mapping[str, Any]


class LoadoutSlotSelectionService:
    """Validate the explicit one-slot-per-role selection used by game writers."""

    def __init__(self, user_dao) -> None:
        self.user_dao = user_dao

    def list_current(self) -> tuple[ResolvedLoadoutSlotSelection, ...]:
        """List all visible current slot plans in deterministic role/slot order."""

        return tuple(
            self._resolve_slot_projection(row["slot"], row["plan"])
            for row in self.user_dao.list_current_loadout_slot_plans()
        )

    def resolve_default_roles(
        self,
        role_names: Iterable[str] | None = None,
        *,
        require_native_snapshot: bool = False,
    ) -> tuple[ResolvedLoadoutSlotSelection, ...]:
        """Pick one current slot per role for a role-based legacy action.

        Bulk actions historically accepted only role names.  With named slots,
        that must resolve through the slot's current-plan pointer rather than
        the old ``is_active`` history flag.  Prefer the primary slot and fall
        back to the earliest current slot when the primary slot is empty.
        """

        requested = tuple(
            dict.fromkeys(
                name.strip()
                for name in (role_names or ())
                if isinstance(name, str) and name.strip()
            )
        )
        current = self.list_current()
        by_role: dict[str, list[ResolvedLoadoutSlotSelection]] = {}
        for selection in current:
            by_role.setdefault(selection.role_name, []).append(selection)
        targets = requested or tuple(by_role)
        selected_ids: list[int] = []
        for role_name in targets:
            candidates = by_role.get(role_name, [])
            if not candidates:
                raise UserDataValidationError(
                    f"角色 [{role_name}] 尚未保存当前配装槽位方案"
                )
            selected = next(
                (row for row in candidates if row.slot_key == "primary"),
                candidates[0],
            )
            selected_ids.append(selected.slot_id)
        return self.resolve(
            selected_ids,
            require_native_snapshot=require_native_snapshot,
        )

    def resolve(
        self,
        slot_ids: Iterable[int],
        *,
        require_native_snapshot: bool = False,
    ) -> tuple[ResolvedLoadoutSlotSelection, ...]:
        """Resolve and validate user-selected slots before any game-side input.

        A target set may contain at most one current slot per character.  Physical
        equipment cannot be assigned to two selected roles, even if neither plan
        is allocation-locked.  When ``require_native_snapshot`` is set, every
        selected plan must originate from an nte-core snapshot.
        """

        raw_slot_ids = tuple(int(slot_id) for slot_id in slot_ids)
        if not raw_slot_ids:
            raise UserDataValidationError("至少选择一个配装槽位")
        if len(set(raw_slot_ids)) != len(raw_slot_ids):
            raise UserDataValidationError("装配目标中包含重复的配装槽位")

        selections: list[ResolvedLoadoutSlotSelection] = []
        character_slots: dict[int, ResolvedLoadoutSlotSelection] = {}
        role_slots: dict[str, ResolvedLoadoutSlotSelection] = {}
        equipment_owner: dict[tuple[int, int], ResolvedLoadoutSlotSelection] = {}
        for slot_id in raw_slot_ids:
            slot = self.user_dao.get_loadout_slot(slot_id)
            if slot is None or slot.get("is_archived"):
                raise UserDataValidationError(f"配装槽位 {slot_id} 不存在或已归档")
            plan = slot.get("current_plan")
            if not isinstance(plan, Mapping):
                raise UserDataValidationError(
                    f"配装槽位 [{slot.get('slot_name') or slot_id}] 尚未保存方案"
                )
            selection = self._resolve_slot_projection(slot, plan)
            previous = character_slots.setdefault(selection.character_id, selection)
            if previous is not selection:
                raise UserDataValidationError(
                    f"角色 [{selection.role_name}] 同时选择了 [{previous.slot_name}] 和 "
                    f"[{selection.slot_name}]；一次装配只能选择一个槽位"
                )
            previous_role = role_slots.setdefault(selection.role_name, selection)
            if previous_role is not selection:
                raise UserDataValidationError(
                    f"角色显示名 [{selection.role_name}] 对应多个角色实例，不能同时装配"
                )
            if require_native_snapshot:
                self._require_native_snapshot(selection)
            self._assert_no_duplicate_equipment(selection, equipment_owner)
            selections.append(selection)
        return tuple(selections)

    def _resolve_slot_projection(
        self,
        slot: Mapping[str, Any],
        plan: Mapping[str, Any],
    ) -> ResolvedLoadoutSlotSelection:
        slot_id = int(slot["slot_id"])
        character_id = int(slot["character_id"])
        if int(plan.get("character_id") or 0) != character_id:
            raise UserDataValidationError(f"配装槽位 {slot_id} 的当前方案不属于该角色")
        payload = plan.get("payload")
        role_name = payload.get("source_role_name") if isinstance(payload, Mapping) else None
        if not isinstance(role_name, str) or not role_name.strip():
            raise UserDataValidationError(
                f"配装槽位 [{slot.get('slot_name') or slot_id}] 的方案缺少角色名称"
            )
        plan_id = int(plan.get("plan_id") or 0)
        if plan_id <= 0:
            raise UserDataValidationError(f"配装槽位 {slot_id} 的当前方案无效")
        return ResolvedLoadoutSlotSelection(
            slot_id=slot_id,
            character_id=character_id,
            slot_key=str(slot.get("slot_key") or ""),
            slot_name=str(slot.get("slot_name") or ""),
            role_name=role_name.strip(),
            plan_id=plan_id,
            source_snapshot_id=(
                int(plan["source_snapshot_id"])
                if plan.get("source_snapshot_id") is not None
                else None
            ),
            plan=plan,
        )

    def _require_native_snapshot(self, selection: ResolvedLoadoutSlotSelection) -> None:
        custom_character_ids = {
            int(row["character_id"])
            for row in self.user_dao.list_custom_characters()
        }
        if selection.character_id in custom_character_ids:
            raise UserDataValidationError(
                f"[{selection.role_name} · {selection.slot_name}] 是自建角色；"
                "极速装配只适用于游戏内角色实例，请使用自动装配"
            )
        snapshot_id = selection.source_snapshot_id
        summary = (
            self.user_dao.inventory_snapshot_summary(snapshot_id)
            if snapshot_id is not None
            else None
        )
        if summary is None or summary.get("source") != "nte_core":
            raise UserDataValidationError(
                f"[{selection.role_name} · {selection.slot_name}] 不来自官方背包快照；"
                "极速装配只支持 nte-core 原生 UID"
            )

    @staticmethod
    def _assert_no_duplicate_equipment(
        selection: ResolvedLoadoutSlotSelection,
        equipment_owner: dict[tuple[int, int], ResolvedLoadoutSlotSelection],
    ) -> None:
        for assignment in selection.plan.get("assignments", ()):
            if not isinstance(assignment, Mapping):
                continue
            uid_slot = int(assignment.get("uid_slot") or 0)
            uid_serial = int(assignment.get("uid_serial") or 0)
            if uid_slot == 0:
                continue
            uid = (uid_slot, uid_serial)
            owner = equipment_owner.setdefault(uid, selection)
            if owner is not selection:
                raise UserDataValidationError(
                    f"装备 UID {uid} 同时出现在 [{owner.role_name} · {owner.slot_name}] 与 "
                    f"[{selection.role_name} · {selection.slot_name}]；请只保留一个装配目标"
                )
