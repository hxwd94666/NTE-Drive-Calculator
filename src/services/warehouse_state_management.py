# 将仓库稳定快照按状态管理规则计算并通过本地核心组件写回游戏。
"""Official SQLite warehouse state management.

This service reuses the full-scan discard/lock rules, but evaluates a pinned
SQLite snapshot and applies the resulting state changes through the already
running nte-core inventory session.  It never relies on screenshot ordering.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

from src.domain.post_actions import summarize_state_changes
from src.integrations.warehouse_state_writer import (
    LiveInventorySync,
    WarehouseStateWriteError,
    WarehouseStateWriter,
)
from src.services.post_action_evaluator import PostActionEvaluator
from src.models.equipment import Drive, Tape
from src.observability import OperationContext, operation_scope
from src.services.sqlite_allocation_inventory import SqliteAllocationInventory
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


class WarehouseStateManagementError(RuntimeError):
    """仓库一键弃置/锁定未满足安全条件或本地核心组件调用失败。"""


@dataclass(frozen=True)
class WarehouseStateManagementPlan:
    snapshot_id: int
    changes: tuple[dict[str, Any], ...]
    filter_summary: dict[str, int]


@dataclass(frozen=True)
class WarehouseStateManagementResult:
    before_snapshot_id: int
    summary: dict[str, int]
    # Keep accepted changes for the timeout fallback.  When a later stable
    # snapshot arrives, after_snapshot_id and verified describe the
    # authoritative reconciliation result.
    changes: tuple[dict[str, Any], ...] = ()
    after_snapshot_id: int | None = None
    verified: bool = False
    verification_error: str | None = None


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
        sync_service: LiveInventorySync,
        *,
        dao_factory=UserDataDao,
        static_dao_factory=StaticGameDataDao,
        state_writer_factory=WarehouseStateWriter,
        config_dir: str | Path | None = None,
        operation_context: OperationContext | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.sync_service = sync_service
        self.state_writer = state_writer_factory(sync_service)
        self.dao_factory = dao_factory
        self.static_dao_factory = static_dao_factory
        self.config_dir = config_dir
        self.operation_context = operation_context or OperationContext.create(
            "warehouse"
        )

    def evaluate(self, config: dict, selected_roles: list[str] | None = None) -> WarehouseStateManagementPlan:
        """Build state changes from the current snapshot without changing game state."""
        with operation_scope(
            self.operation_context,
            started_event="warehouse.state_evaluate_started",
            succeeded_event="warehouse.state_evaluate_succeeded",
            failed_event="warehouse.state_evaluate_failed",
            message="计算仓库状态管理目标",
            selected_role_count=len(selected_roles or ()),
        ) as span:
            plan = self._evaluate(config, selected_roles)
            span.annotate(
                snapshot_id=plan.snapshot_id,
                change_count=len(plan.changes),
                filter_summary=plan.filter_summary,
            )
            return plan

    def _evaluate(
        self,
        config: dict,
        selected_roles: list[str] | None,
    ) -> WarehouseStateManagementPlan:
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
            user_database_path=self.database_path,
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
        context = self.operation_context.with_values(snapshot_id=snapshot_id)
        with operation_scope(
            context,
            started_event="warehouse.manual_plan_started",
            succeeded_event="warehouse.manual_plan_succeeded",
            failed_event="warehouse.manual_plan_failed",
            message="生成仓库手工状态计划",
            target_count=len(targets),
        ) as span:
            plan = self._plan_manual_changes(snapshot_id, targets)
            span.annotate(change_count=len(plan.changes))
            return plan

    def _plan_manual_changes(
        self,
        snapshot_id: int,
        targets: Mapping[str, str],
    ) -> WarehouseStateManagementPlan:
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

    def apply(
        self,
        plan: WarehouseStateManagementPlan,
        *,
        confirmation_timeout: float = 20.0,
        progress_callback: Callable[[str], None] | None = None,
    ) -> WarehouseStateManagementResult:
        """Apply a reviewed plan and reconcile it against a later stable snapshot."""
        context = self.operation_context.with_values(snapshot_id=plan.snapshot_id)
        with operation_scope(
            context,
            started_event="warehouse.state_apply_started",
            succeeded_event="warehouse.state_apply_succeeded",
            failed_event="warehouse.state_apply_failed",
            message="写回仓库装备状态",
            change_count=len(plan.changes),
        ) as span:
            result = self._apply(
                plan,
                confirmation_timeout=confirmation_timeout,
                progress_callback=progress_callback,
            )
            span.annotate(
                summary=result.summary,
                after_snapshot_id=result.after_snapshot_id,
                verified=result.verified,
                verification_error=result.verification_error,
            )
            return result

    def _apply(
        self,
        plan: WarehouseStateManagementPlan,
        *,
        confirmation_timeout: float,
        progress_callback: Callable[[str], None] | None,
    ) -> WarehouseStateManagementResult:
        self._report_progress(
            progress_callback,
            "正在检查背包同步状态和核心组件能力…",
        )
        try:
            self.state_writer.ensure_ready()
        except WarehouseStateWriteError as exc:
            raise WarehouseStateManagementError(str(exc)) from exc

        with self.dao_factory(self.database_path) as user_dao:
            current_snapshot_id = user_dao.current_inventory_snapshot_id()
            if current_snapshot_id != plan.snapshot_id:
                raise WarehouseStateManagementError("背包快照已更新，请刷新仓库并重新确认管理目标")
            current_rows = {
                (row["uid_slot"], row["uid_serial"]): row
                for row in user_dao.list_inventory_items(plan.snapshot_id)
            }
            applied_changes: list[dict[str, Any]] = []
            total_changes = len(plan.changes)
            for index, change in enumerate(plan.changes, 1):
                equipment = dict(change["equipment"])
                row = current_rows.get((equipment["slot"], equipment["serial"]))
                if row is None:
                    raise WarehouseStateManagementError("目标装备已不在当前稳定快照中")
                self._report_progress(
                    progress_callback,
                    f"正在向游戏提交第 {index}/{total_changes} 件装备状态…",
                )
                try:
                    self.state_writer.apply_one(
                        row,
                        str(change["target_state"]),
                        equipment,
                    )
                except WarehouseStateWriteError as exc:
                    raise WarehouseStateManagementError(str(exc)) from exc
                # Rule-generated changes already have the presentation UID,
                # while manually-created plans do not.  Return one consistent
                # form so the warehouse can update the affected card at once.
                applied_change = dict(change)
                applied_change["uid"] = str(applied_change.get("uid") or _compat_uid(row))
                applied_changes.append(applied_change)

        if not plan.changes:
            self._report_progress(
                progress_callback,
                "当前状态无需修改。",
            )
            return WarehouseStateManagementResult(
                before_snapshot_id=plan.snapshot_id,
                summary=summarize_state_changes([]),
                changes=(),
                after_snapshot_id=plan.snapshot_id,
                verified=True,
            )
        self._report_progress(
            progress_callback,
            "修改指令已全部提交，正在等待游戏产生新的完整背包快照…",
        )
        after_snapshot_id, verified, verification_error = (
            self._wait_for_confirmation(
                plan.snapshot_id,
                tuple(applied_changes),
                timeout=confirmation_timeout,
                progress_callback=progress_callback,
            )
        )
        return WarehouseStateManagementResult(
            before_snapshot_id=plan.snapshot_id,
            summary=summarize_state_changes(list(plan.changes)),
            changes=tuple(applied_changes),
            after_snapshot_id=after_snapshot_id,
            verified=verified,
            verification_error=verification_error,
        )

    def _wait_for_confirmation(
        self,
        before_snapshot_id: int,
        changes: tuple[dict[str, Any], ...],
        *,
        timeout: float,
        progress_callback: Callable[[str], None] | None,
    ) -> tuple[int | None, bool, str | None]:
        """Wait through intermediate snapshots until every accepted change matches."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        cursor = before_snapshot_id
        latest_snapshot_id: int | None = None
        remaining_mismatches = len(changes)

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if latest_snapshot_id is None:
                    message = (
                        "未在限定时间内收到修改后的新背包快照；"
                        "当前 nte-core 协议不能主动要求游戏刷新，"
                        "需等待游戏后续产生完整背包数据"
                    )
                else:
                    message = (
                        f"已收到新快照 #{latest_snapshot_id}，但仍有 "
                        f"{remaining_mismatches} 件状态尚未确认"
                    )
                return latest_snapshot_id, False, message
            try:
                state = self.sync_service.wait_for_snapshot(
                    after_snapshot_id=cursor,
                    timeout=remaining,
                )
            except TimeoutError:
                if latest_snapshot_id is None:
                    message = (
                        "未在限定时间内收到修改后的新背包快照；"
                        "当前 nte-core 协议不能主动要求游戏刷新，"
                        "需等待游戏后续产生完整背包数据"
                    )
                else:
                    message = (
                        f"已收到新快照 #{latest_snapshot_id}，但仍有 "
                        f"{remaining_mismatches} 件状态尚未确认"
                    )
                return latest_snapshot_id, False, message
            except Exception as exc:
                return (
                    latest_snapshot_id,
                    False,
                    f"等待新背包快照时同步服务异常（{type(exc).__name__}）",
                )

            snapshot_id = getattr(state, "last_snapshot_id", None)
            if (
                not isinstance(snapshot_id, int)
                or snapshot_id <= cursor
            ):
                return (
                    latest_snapshot_id,
                    False,
                    "同步服务未返回递增的新背包快照编号",
                )
            latest_snapshot_id = snapshot_id
            cursor = snapshot_id
            self._report_progress(
                progress_callback,
                f"已收到新快照 #{snapshot_id}，正在核对修改结果…",
            )
            with self.dao_factory(self.database_path) as user_dao:
                rows = user_dao.list_inventory_items(snapshot_id)
            remaining_mismatches = self._count_state_mismatches(
                rows,
                changes,
            )
            if remaining_mismatches == 0:
                self._report_progress(
                    progress_callback,
                    f"新快照 #{snapshot_id} 已确认全部修改。",
                )
                return snapshot_id, True, None
            self._report_progress(
                progress_callback,
                f"快照 #{snapshot_id} 尚有 {remaining_mismatches} 件未确认，继续等待…",
            )

    @staticmethod
    def _report_progress(
        callback: Callable[[str], None] | None,
        message: str,
    ) -> None:
        if callback is not None:
            callback(message)

    @staticmethod
    def _count_state_mismatches(
        rows: list[dict[str, Any]],
        changes: tuple[dict[str, Any], ...],
    ) -> int:
        by_uid = {
            (row.get("uid_slot"), row.get("uid_serial")): row
            for row in rows
        }
        mismatches = 0
        for change in changes:
            equipment = change.get("equipment")
            if not isinstance(equipment, Mapping):
                mismatches += 1
                continue
            row = by_uid.get(
                (equipment.get("slot"), equipment.get("serial"))
            )
            if (
                row is None
                or _current_state(row) != str(change.get("target_state") or "")
            ):
                mismatches += 1
        return mismatches
