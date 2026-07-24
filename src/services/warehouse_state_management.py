# 将仓库稳定快照按状态管理规则计算并通过本地核心组件写回游戏。
"""Official SQLite warehouse state management.

This service reuses the full-scan discard/lock rules, but evaluates a pinned
SQLite snapshot and applies the resulting state changes through the already
running nte-core inventory session.  It never relies on screenshot ordering.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from src.features.scanning.post_action_evaluator import PostActionEvaluator
from src.features.scanning.post_actions import summarize_state_changes
from src.models.equipment import Drive, Tape
from src.services.sqlite_allocation_inventory import SqliteAllocationInventory
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


class WarehouseStateManagementError(RuntimeError):
    """仓库一键弃置/锁定未满足安全条件或本地核心组件调用失败。"""


class _LiveInventorySync(Protocol):
    @property
    def state(self) -> Any: ...

    @property
    def is_running(self) -> bool: ...

    @property
    def core_hello_result(self) -> dict[str, Any] | None: ...

    def set_item_discarded(self, *, equipment: Mapping[str, Any], discarded: bool) -> Any: ...

    def set_item_locked(self, *, equipment: Mapping[str, Any], locked: bool) -> Any: ...

@dataclass(frozen=True)
class WarehouseStateManagementPlan:
    snapshot_id: int
    changes: tuple[dict[str, Any], ...]
    filter_summary: dict[str, int]


@dataclass(frozen=True)
class WarehouseStateManagementResult:
    before_snapshot_id: int
    summary: dict[str, int]


def _compat_uid(row: Mapping[str, Any]) -> str:
    prefix = "module" if row.get("kind") == "module" else "core"
    return f"nte-{prefix}-{row['uid_slot']}-{row['uid_serial']}"


def _current_state(row: Mapping[str, Any]) -> str:
    if row.get("discarded"):
        return "discarded"
    if row.get("locked"):
        return "locked"
    return "normal"


def _equipment_uid(row: Mapping[str, Any]) -> dict[str, int]:
    slot, serial = row.get("uid_slot"), row.get("uid_serial")
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in (slot, serial)):
        raise WarehouseStateManagementError("稳定快照包含无效的装备 UID")
    return {"slot": slot, "serial": serial}


class WarehouseStateManagementService:
    """Evaluate and apply discard/lock rules for one immutable inventory snapshot."""

    def __init__(
        self,
        database_path: str | Path,
        sync_service: _LiveInventorySync,
        *,
        dao_factory=UserDataDao,
        static_dao_factory=StaticGameDataDao,
        config_dir: str | Path | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.sync_service = sync_service
        self.dao_factory = dao_factory
        self.static_dao_factory = static_dao_factory
        self.config_dir = config_dir

    def evaluate(self, config: dict, selected_roles: list[str] | None = None) -> WarehouseStateManagementPlan:
        """Build state changes from the current snapshot without changing game state."""
        with self.dao_factory(self.database_path) as user_dao, self.static_dao_factory() as static_dao:
            snapshot_id = user_dao.current_inventory_snapshot_id()
            if snapshot_id is None:
                raise WarehouseStateManagementError("尚无稳定背包快照，无法管理仓库")
            projection = SqliteAllocationInventory(user_dao, static_dao).build(snapshot_id)
            snapshot_id = projection.snapshot_id
            source_rows = user_dao.list_inventory_items(snapshot_id)

        source_by_uid = {_compat_uid(row): row for row in source_rows}
        inventory = []
        parsed_items = []
        for index, payload in enumerate(projection.items, 1):
            source = source_by_uid.get(payload["uid"])
            if source is None:
                raise WarehouseStateManagementError("稳定快照投影与原始装备 UID 不一致")
            item = Drive(**payload) if payload["item_type"] == "drive" else Tape(**payload)
            inventory.append(item)
            parsed_items.append((index, item, _current_state(source)))

        evaluation = PostActionEvaluator(
            post_actions_config=config,
            selected_roles=selected_roles,
            config_dir=self.config_dir,
        ).evaluate(parsed_items, inventory)
        changes: list[dict[str, Any]] = []
        for change in evaluation.state_changes:
            source = source_by_uid.get(str(change.get("uid") or ""))
            if source is None:
                raise WarehouseStateManagementError("状态管理目标不在固定稳定快照中")
            enriched = dict(change)
            enriched["equipment"] = _equipment_uid(source)
            changes.append(enriched)
        return WarehouseStateManagementPlan(
            snapshot_id=snapshot_id,
            changes=tuple(changes),
            filter_summary=dict(evaluation.filter_summary),
        )

    def plan_manual_changes(
        self,
        snapshot_id: int,
        targets: Mapping[str, str],
    ) -> WarehouseStateManagementPlan:
        """Prepare user-selected card edits against one fixed official snapshot.

        ``targets`` is keyed by the presentation UID (``nte-module-slot-serial``
        or ``nte-core-slot-serial``).  The UI only stores this small local diff;
        the authoritative current state remains the SQLite snapshot until save.
        """
        if not isinstance(snapshot_id, int) or snapshot_id <= 0:
            raise WarehouseStateManagementError("没有可保存的稳定背包快照")
        with self.dao_factory(self.database_path) as user_dao:
            if user_dao.current_inventory_snapshot_id() != snapshot_id:
                raise WarehouseStateManagementError("游戏背包已更新，请等待仓库自动刷新后重新编辑")
            rows = user_dao.list_inventory_items(snapshot_id)
        by_uid = {_compat_uid(row): row for row in rows}
        changes: list[dict[str, Any]] = []
        for uid, target_state in targets.items():
            if target_state not in {"normal", "locked", "discarded"}:
                raise WarehouseStateManagementError(f"仓库中包含未知目标状态：{target_state}")
            row = by_uid.get(str(uid))
            if row is None:
                raise WarehouseStateManagementError("已编辑的装备不在当前稳定背包快照中")
            if _current_state(row) != target_state:
                changes.append(
                    {
                        "uid": str(uid),
                        "target_state": target_state,
                        "equipment": _equipment_uid(row),
                    }
                )
        return WarehouseStateManagementPlan(
            snapshot_id=snapshot_id,
            changes=tuple(changes),
            filter_summary={},
        )

    def apply(self, plan: WarehouseStateManagementPlan) -> WarehouseStateManagementResult:
        """Apply a pre-reviewed plan through nte-core without blocking on a later inventory snapshot."""
        state = self.sync_service.state
        if not self.sync_service.is_running or getattr(state, "phase", None) != "listening":
            raise WarehouseStateManagementError("背包同步必须处于稳定监听状态才能管理仓库")
        capabilities = (self.sync_service.core_hello_result or {}).get("capabilities", [])
        if not isinstance(capabilities, list) or "equipment" not in capabilities:
            raise WarehouseStateManagementError("当前 nte-core 不支持 equipment 状态管理能力")

        with self.dao_factory(self.database_path) as user_dao:
            current_snapshot_id = user_dao.current_inventory_snapshot_id()
            if current_snapshot_id != plan.snapshot_id:
                raise WarehouseStateManagementError("背包快照已更新，请刷新仓库并重新确认管理目标")
            current_rows = {
                (row["uid_slot"], row["uid_serial"]): row
                for row in user_dao.list_inventory_items(plan.snapshot_id)
            }
            for change in plan.changes:
                equipment = dict(change["equipment"])
                row = current_rows.get((equipment["slot"], equipment["serial"]))
                if row is None:
                    raise WarehouseStateManagementError("目标装备已不在当前稳定快照中")
                self._apply_one(row, str(change["target_state"]), equipment)

        if not plan.changes:
            return WarehouseStateManagementResult(
                before_snapshot_id=plan.snapshot_id,
                summary=summarize_state_changes([]),
            )
        return WarehouseStateManagementResult(
            before_snapshot_id=plan.snapshot_id,
            summary=summarize_state_changes(list(plan.changes)),
        )

    def _apply_one(self, row: Mapping[str, Any], target_state: str, equipment: dict[str, int]) -> None:
        if target_state not in {"normal", "locked", "discarded"}:
            raise WarehouseStateManagementError(f"未知目标状态：{target_state}")
        discarded = bool(row.get("discarded"))
        locked = bool(row.get("locked"))
        if target_state == "normal":
            if discarded:
                self.sync_service.set_item_discarded(equipment=equipment, discarded=False)
            if locked:
                self.sync_service.set_item_locked(equipment=equipment, locked=False)
        elif target_state == "locked":
            if discarded:
                self.sync_service.set_item_discarded(equipment=equipment, discarded=False)
            if not locked:
                self.sync_service.set_item_locked(equipment=equipment, locked=True)
        else:
            if locked:
                self.sync_service.set_item_locked(equipment=equipment, locked=False)
            if not discarded:
                self.sync_service.set_item_discarded(equipment=equipment, discarded=True)
