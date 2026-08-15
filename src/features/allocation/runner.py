# 执行分配任务并处理保存和归档。
"""MainWindow methods for allocation."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout

from src.app.theme import current_style_sheet
from src.app.workers import WorkerThread
from src.optimizer.plan_diff import build_plan_diff
from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_ADDED_UIDS,
    DIFF_CHANGED,
    DIFF_REMOVED,
    EQUIP_IS_CHANGED,
    EQUIP_UID,
    PLAN_ASSIGNED_TAPE,
    PLAN_BLUEPRINT,
    PLAN_CHANGED_UIDS,
    PLAN_SCORE,
    PLAN_VALID,
    ROLE_BLUEPRINT_LAYOUT,
    ROLE_EQUIPPED_DRIVES,
    ROLE_EQUIPPED_TAPE,
    plan_drives,
)
from src.services.sqlite_allocation_inventory import SqliteAllocationInventory
from src.features.allocation.slot_plan_diff import (
    selected_slot_plan_diff,
    single_slot_loadout_state,
)
from src.services.allocation_lock_service import (
    AllocationLockSnapshot,
    build_allocation_lock_snapshot,
    filter_allocation_request_for_locks,
    verify_allocation_lock_snapshot,
)
from src.services.saved_state_loadout_bridge import (
    SavedStateLoadoutBridge,
    resolve_character_id_for_allocation_role,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import logger

__all__ = [
    "_run_allocation",
    "_start_allocation_worker",
    "_confirm_unsaved_allocation_before_recompute",
    "_on_done",
    "_on_exec_error",
    "_save_alloc",
    "_archive_pending_screenshots",
    "AllocationRunResult",
]


@dataclass(frozen=True)
class AllocationRunResult:
    """Worker result bound to the stable snapshot and lock state it consumed."""

    plans: dict[str, Any]
    snapshot_id: int
    lock_snapshot: AllocationLockSnapshot


def _allocation_paths(window: Any) -> tuple[Path, Path, Path, Path, Path]:
    context = getattr(window, "app_context", None)
    if context is None:
        database_path = getattr(window, "user_database_path", None)
        config_dir = getattr(window, "config_dir", None)
        user_config_dir = getattr(window, "user_config_dir", None)
        screenshot_dir = getattr(window, "screenshot_dir", None)
        static_database_path = getattr(window, "static_database_path", None)
        if any(
            value is None
            for value in (
                database_path,
                config_dir,
                user_config_dir,
                screenshot_dir,
                static_database_path,
            )
        ):
            raise RuntimeError("分配功能缺少 AppContext 或显式路径依赖")
        assert database_path is not None
        assert config_dir is not None
        assert user_config_dir is not None
        assert screenshot_dir is not None
        assert static_database_path is not None
        return (
            Path(database_path),
            Path(config_dir),
            Path(user_config_dir),
            Path(screenshot_dir),
            Path(static_database_path),
        )
    return (
        Path(context.account.user_database_path),
        Path(context.paths.config_dir),
        Path(context.account.user_config_dir),
        Path(context.account.screenshot_dir),
        Path(context.paths.static_database_path),
    )


def _run_allocation(
    self: Any,
    strat: str,
    sel: list[str],
    cs: dict[str, Any],
    tape_main_filters: dict[str, Any] | None = None,
    crit_priority_modes: dict[str, Any] | None = None,
    set_effect_modes: dict[str, Any] | None = None,
    priority_groups: Any = None,
    crit_rate_caps: dict[str, Any] | None = None,
    custom_weapons: dict[str, Any] | None = None,
) -> Any:
    try:
        database_path, config_dir, user_config_dir, _, static_database_path = _allocation_paths(self)
        logger.info(f"开始分配计算: 策略={strat}, 角色={sel}")
        if not database_path.is_file():
            raise RuntimeError("尚无官方背包数据，请先完成背包同步并生成稳定快照。")
        with UserDataDao(database_path) as user_dao, StaticGameDataDao(static_database_path) as static_dao:
            snapshot_id = user_dao.current_inventory_snapshot_id()
            if snapshot_id is None:
                raise RuntimeError("尚无稳定背包快照，请先在首页启动背包同步并进入游戏。")
            projection = SqliteAllocationInventory(user_dao, static_dao).build(snapshot_id)
            lock_snapshot = build_allocation_lock_snapshot(
                user_dao,
                inventory_snapshot_id=projection.snapshot_id,
            )
        unlocked_sel, unlocked_priority_groups = filter_allocation_request_for_locks(
            sel,
            priority_groups,
            lock_snapshot,
        )
        allocation_options = {
            "tape_main_filters": tape_main_filters or {},
            "crit_priority_modes": crit_priority_modes or {},
            "set_effect_modes": set_effect_modes or {},
            "priority_groups": unlocked_priority_groups,
            "crit_rate_caps": crit_rate_caps or {},
            "custom_weapons": custom_weapons or {},
        }
        logger.info(
            f"使用官方背包稳定快照 {projection.snapshot_id} 计算："
            f"候选 {len(projection.items)} 件（其中弃置标记 {projection.discarded_count} 件，仍参与计算）"
        )
        if lock_snapshot.locked_role_names:
            logger.info(
                f"配装锁定已保留 {len(lock_snapshot.locked_role_names)} 个角色、"
                f"排除 {len(lock_snapshot.reserved_uids)} 件装备："
                f"{'、'.join(sorted(lock_snapshot.locked_role_names))}"
            )
        # 求解器只接收本次固定 SQLite 快照的内存投影，不再回退到旧背包 JSON。
        from src.app.facade import NTEAppFacade

        a = NTEAppFacade(
            config_dir=str(config_dir),
            user_config_dir=str(user_config_dir),
            user_database_path=database_path,
        )
        if unlocked_sel:
            fp, _ = a.execute_allocation_inventory(
                list(projection.items),
                unlocked_sel,
                cs,
                strat,
                locked_uids=set(lock_snapshot.reserved_uids),
                **allocation_options,
            )
        else:
            fp = {}
        logger.info(f"分配计算完成: result_type={type(fp).__name__}")
        return AllocationRunResult(
            plans=fp,
            snapshot_id=projection.snapshot_id,
            lock_snapshot=lock_snapshot,
        )
    except Exception as e:
        import traceback as tb

        logger.error(f"_run_allocation 内部异常: {e}\n{tb.format_exc()}")
        raise


def _start_allocation_worker(self: Any) -> None:
    logger.info("启动分配工作线程...")
    self._worker = WorkerThread(
        target=lambda: self._run_allocation(
            self._pending_strat,
            self._pending_sel,
            self._pending_cs,
            getattr(self, "_pending_tape_main_filters", {}),
            getattr(self, "_pending_crit_priority_modes", {}),
            getattr(self, "_pending_set_effect_modes", {}),
            getattr(self, "_pending_priority_groups", None),
            getattr(self, "_pending_crit_rate_caps", {}),
            getattr(self, "_pending_custom_weapons", {}),
        ),
        parent=self,
    )
    self._worker.result_ready.connect(self._on_done)
    self._worker.error.connect(self._on_exec_error)
    self._worker.start()
    logger.info("分配线程已启动")


def _active_sqlite_loadout_state(
    database_path: str | Path,
) -> dict[str, dict[str, Any]]:
    """Build a baseline only for roles that have exactly one visible slot."""

    with UserDataDao(database_path) as user_dao:
        return single_slot_loadout_state(user_dao)


def _sqlite_allocation_plan_diff(
    database_path: str | Path,
    final_plan: dict[str, Any],
) -> dict[str, Any]:
    """Compare with a slot only when it is unambiguous before saving."""

    return build_plan_diff(_active_sqlite_loadout_state(database_path), final_plan)


def _calculation_plan_diff(
    self: Any,
    final_plan: dict[str, Any],
) -> dict[str, Any]:
    """Prefer active SQLite plans; retain a no-database test-host fallback."""

    try:
        database_path = _allocation_paths(self)[0]
        return _sqlite_allocation_plan_diff(database_path, final_plan)
    except Exception as exc:
        logger.warning(f"读取 SQLite 配装差异失败，改用无数据库兼容基线：{exc}")
    return build_plan_diff({}, final_plan)


def _persistable_plan_diff(
    role_diff: dict[str, Any] | None,
) -> dict[str, Any]:
    """Convert in-memory diff sets to JSON-compatible plan payload data."""

    source = role_diff or {}
    return {
        DIFF_CHANGED: bool(source.get(DIFF_CHANGED)),
        DIFF_ADDED_UIDS: sorted(str(uid) for uid in (source.get(DIFF_ADDED_UIDS) or ()) if uid),
        DIFF_ADDED: [dict(item) for item in (source.get(DIFF_ADDED) or ()) if isinstance(item, dict)],
        DIFF_REMOVED: [dict(item) for item in (source.get(DIFF_REMOVED) or ()) if isinstance(item, dict)],
    }


def _plan_changed_uids(
    plan: dict[str, Any],
    role_diff: dict[str, Any] | None,
) -> set[str]:
    """Collect change markers only when replacing a non-empty saved slot."""

    if not bool((role_diff or {}).get(DIFF_CHANGED)):
        return set()
    changed = {
        str(uid)
        for uid in (plan.get(PLAN_CHANGED_UIDS, set()) or ())
        if uid
    }
    for item in [plan.get(PLAN_ASSIGNED_TAPE), *plan_drives(plan)]:
        value = (
            item.get(EQUIP_IS_CHANGED)
            if isinstance(item, dict)
            else getattr(item, EQUIP_IS_CHANGED, False)
        )
        uid = (
            item.get(EQUIP_UID)
            if isinstance(item, dict)
            else getattr(item, EQUIP_UID, "")
        )
        if value and uid:
            changed.add(str(uid))
    return changed


def _plan_assignment_scores(
    role_name: str,
    plan: dict[str, Any],
) -> dict[str, float]:
    """Freeze each selected item's role score beside the aggregate score."""

    result: dict[str, float] = {}
    for item in [plan.get(PLAN_ASSIGNED_TAPE), *plan_drives(plan)]:
        if item is None:
            continue
        uid = str(
            item.get(EQUIP_UID, "")
            if isinstance(item, dict)
            else getattr(item, EQUIP_UID, "")
        )
        role_scores = (
            item.get("role_scores", {})
            if isinstance(item, dict)
            else getattr(item, "role_scores", {})
        ) or {}
        if uid:
            result[uid] = float(role_scores.get(role_name, 0.0) or 0.0)
    return result


def _plan_tape_main_values(plan: dict[str, Any]) -> dict[str, float]:
    """Freeze the calculated card main value in the saved plan payload."""

    tape = plan.get(PLAN_ASSIGNED_TAPE)
    if tape is None:
        return {}
    uid = str(tape.get(EQUIP_UID, "") if isinstance(tape, dict) else getattr(tape, EQUIP_UID, ""))
    value = tape.get("main_value") if isinstance(tape, dict) else getattr(tape, "main_value", None)
    try:
        return {uid: float(value)} if uid and value is not None else {}
    except (TypeError, ValueError):
        return {}


def _confirm_unsaved_allocation_before_recompute(self: Any) -> bool:
    if not self.final_plan or not self._allocation_dirty:
        return True
    if self._ui_preferences.get("skip_unsaved_allocation_prompt"):
        self._allocation_dirty = False
        return True
    dlg = QDialog(getattr(self, "dialog_parent", None))
    dlg.setWindowTitle("当前配装尚未保存")
    dlg.setStyleSheet(current_style_sheet())
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(18, 18, 18, 18)
    layout.setSpacing(14)
    msg = QLabel("重新执行计算会覆盖当前计算结果，是否先保存当前配装？")
    msg.setWordWrap(True)
    layout.addWidget(msg)
    row = QHBoxLayout()
    row.setSpacing(10)
    dont_btn = QPushButton("不再提醒")
    dont_btn.setObjectName("btnDanger")
    skip_btn = QPushButton("不保存")
    save_btn = QPushButton("保存")
    save_btn.setObjectName("btnPrimary")
    row.addWidget(dont_btn)
    row.addWidget(skip_btn)
    row.addWidget(save_btn)
    layout.addLayout(row)
    choice: dict[str, str | None] = {"value": None}

    def select(value: str) -> None:
        choice["value"] = value
        dlg.accept()

    dont_btn.clicked.connect(lambda: select("never"))
    skip_btn.clicked.connect(lambda: select("skip"))
    save_btn.clicked.connect(lambda: select("save"))
    dlg.exec()
    if choice["value"] == "save":
        return self._save_alloc(show_message=False)
    if choice["value"] == "never":
        self._ui_preferences["skip_unsaved_allocation_prompt"] = True
        self._save_ui_preferences()
        self._allocation_dirty = False
        return True
    if choice["value"] == "skip":
        self._allocation_dirty = False
        return True
    return False


def _on_done(self: Any, r: Any) -> None:
    try:
        logger.info(
            f"_on_done 收到结果: type={type(r).__name__}, keys={list(r.keys()) if isinstance(r, dict) else 'N/A'}"
        )
        if not isinstance(r, AllocationRunResult):
            raise RuntimeError("分配线程返回了未绑定快照的结果")
        self.final_plan = r.plans
        self._pending_allocation_snapshot_id = r.snapshot_id
        self._allocation_lock_snapshot = r.lock_snapshot
        self.btn_run.setEnabled(True)
        self.btn_run.setText("⚡  开始计算")
        self._allocation_custom_weapons = dict(getattr(self, "_pending_custom_weapons", {}) or {})
        # The old JSON-state path was removed.  Comparing with the active
        # SQLite plans restores NEW/CHANGE labels and the per-role diff button.
        self.allocation_plan_diff = _calculation_plan_diff(self, self.final_plan)
        self._allocation_dirty = bool(self.final_plan)
        self._render_results(self.final_plan)
        logger.info("_render_results 完成")
    except Exception as e:
        import traceback as tb

        logger.error(f"_on_done 异常: {e}\n{tb.format_exc()}")
        QMessageBox.critical(self.dialog_parent, "渲染失败", f"{e}")


def _on_exec_error(self: Any, err: str) -> None:
    self.btn_run.setEnabled(True)
    self.btn_run.setText("⚡  开始计算")
    QMessageBox.critical(
        self.dialog_parent,
        "计算失败",
        f"发生错误:\n{err}",
    )


def _select_allocation_save_slots(
    self: Any,
    user_dao: UserDataDao,
    static_dao: StaticGameDataDao,
    snapshot_id: int,
) -> dict[str, tuple[int, int]] | None:
    """Choose existing role slots before any calculation plan is persisted."""

    targets: dict[str, tuple[int, int]] = {}
    for role_name, plan in self.final_plan.items():
        if not isinstance(plan, dict) or not plan.get(PLAN_VALID):
            continue
        character_id = resolve_character_id_for_allocation_role(
            role_name, static_dao, user_dao, snapshot_id=snapshot_id
        )
        slots = user_dao.list_loadout_slots(character_id)
        if not slots:
            user_dao.create_loadout_slot(character_id, role_name, slot_key="primary")
            slots = user_dao.list_loadout_slots(character_id)
        if len(slots) == 1:
            slot = slots[0]
        else:
            labels = [
                str(slot["slot_name"])
                + ("（已锁定）" if (slot.get("current_plan") or {}).get("allocation_locked") else "")
                for slot in slots
            ]
            selected, accepted = QInputDialog.getItem(
                self.dialog_parent,
                "选择保存槽位",
                f"[{role_name}] 的计算结果保存到：",
                labels,
                0,
                False,
            )
            if not accepted:
                return None
            slot = slots[labels.index(selected)]
        if (slot.get("current_plan") or {}).get("allocation_locked"):
            QMessageBox.warning(self.dialog_parent, "保存方案", f"[{role_name}] 选择的槽位已锁定，不能覆盖。")
            return None
        targets[role_name] = (character_id, int(slot["slot_id"]))
    return targets


def _save_alloc(self: Any, show_message: bool = True) -> bool:
    if not self.final_plan:
        return False
    try:
        database_path, _, _, _, static_database_path = _allocation_paths(self)
        snapshot_id = getattr(self, "_pending_allocation_snapshot_id", None)
        if snapshot_id is None:
            raise RuntimeError("本次计算未绑定官方背包快照，请重新执行计算。")
        saved_roles = []
        with UserDataDao(database_path) as user_dao, StaticGameDataDao(static_database_path) as static_dao:
            lock_snapshot = getattr(self, "_allocation_lock_snapshot", None)
            if not isinstance(lock_snapshot, AllocationLockSnapshot):
                raise RuntimeError("本次计算缺少配装锁定快照，请重新执行计算。")
            if lock_snapshot.inventory_snapshot_id != snapshot_id:
                raise RuntimeError("计算快照与配装锁定快照不一致，请重新执行计算。")
            verify_allocation_lock_snapshot(user_dao, lock_snapshot)
            targets = _select_allocation_save_slots(
                self,
                user_dao,
                static_dao,
                int(snapshot_id),
            )
            if targets is None:
                return False
            # Selection is made only at save time for multi-slot roles.  Rebuild
            # the comparison here so slot B never inherits slot A's baseline.
            selected_slot_diffs = selected_slot_plan_diff(
                user_dao,
                self.final_plan,
                targets,
            )
            self.allocation_plan_diff = selected_slot_diffs
            bridge = SavedStateLoadoutBridge(user_dao, static_dao)
            for role_name, plan in self.final_plan.items():
                if not isinstance(plan, dict) or not plan.get(PLAN_VALID):
                    continue
                character_id, slot_id = targets[role_name]
                role_diff = (getattr(self, "allocation_plan_diff", {}) or {}).get(role_name, {})
                bridge.save_role_plan(
                    role_name=role_name,
                    role_state=_role_state_from_plan(plan),
                    character_id=character_id,
                    snapshot_id=snapshot_id,
                    name=f"计算方案：{role_name}",
                    score=float(plan.get(PLAN_SCORE, 0.0) or 0.0),
                    payload={
                        "schema": "allocation-official-snapshot-v1",
                        "source": "allocation",
                        "source_role_name": role_name,
                        "strategy": getattr(self, "_pending_strat", ""),
                        "last_diff": _persistable_plan_diff(role_diff),
                        "changed_uids": sorted(_plan_changed_uids(plan, role_diff)),
                        "assignment_scores": _plan_assignment_scores(
                            role_name,
                            plan,
                        ),
                        # The card's full-level main stat is part of this
                        # computed plan, not a value to reconstruct at every
                        # later presentation pass.
                        "tape_main_values": _plan_tape_main_values(plan),
                    },
                    slot_id=slot_id,
                )
                saved_roles.append(role_name)
        if not saved_roles:
            raise RuntimeError("本次计算没有可保存的有效方案。")
        self._allocation_dirty = False
        # The saved target slot is now known, so update the visible calculation
        # comparison with the same baseline that was persisted into the plan.
        self._render_results(self.final_plan)
        # Active plans are the character-page equipment source.  Refresh both
        # projections immediately so a saved calculation is visible as the
        # role's drive/core context without writing any template/profile data.
        refresh_roles = getattr(self, "_refresh_my_role", None)
        if callable(refresh_roles):
            refresh_roles()
        refresh_equipment = getattr(self, "_refresh_equip", None)
        if callable(refresh_equipment):
            refresh_equipment()
        if show_message:
            QMessageBox.information(
                self.dialog_parent,
                "保存成功",
                f"已将 {len(saved_roles)} 个方案保存到官方 SQLite 数据库，并同步到角色与配装页面。",
            )
        return True
    except Exception as e:
        QMessageBox.critical(self.dialog_parent, "失败", str(e))
        return False


def _role_state_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """构造仅存在于内存中的转换对象；不读写旧 JSON。"""
    board = []
    for row in plan.get(PLAN_BLUEPRINT, {}).get("board", []) or []:
        board.append(["XX" if cell == -1 else "0" if cell == 0 else str(cell) for cell in row])
    tape = plan.get(PLAN_ASSIGNED_TAPE)
    return {
        ROLE_BLUEPRINT_LAYOUT: board,
        ROLE_EQUIPPED_TAPE: {EQUIP_UID: tape.uid} if tape is not None else None,
        ROLE_EQUIPPED_DRIVES: [{EQUIP_UID: drive.uid, "shape_id": drive.shape_id} for drive in plan_drives(plan)],
    }


def _archive_pending_screenshots(self: Any) -> int:
    paths = list(getattr(self, "_pending_archive_paths", []) or [])
    if not paths:
        return 0
    archive_dir = _allocation_paths(self)[3] / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived_count = 0
    for src in paths:
        src_path = Path(src)
        if not src_path.exists():
            continue
        dst = archive_dir / src_path.name
        base = dst.with_suffix("")
        ext = dst.suffix
        suffix = 1
        while dst.exists():
            dst = Path(f"{base}_{suffix}{ext}")
            suffix += 1
        shutil.move(str(src_path), str(dst))
        archived_count += 1
    self._pending_archive_paths = []
    if archived_count:
        logger.success(f"已归档 {archived_count} 张已保存配装的截图。")
    return archived_count


class AllocationController(QObject):
    """Own one calculation worker, its frozen request, and save state."""

    def __init__(
        self,
        *,
        app_context: Any,
        dialog_parent: QObject,
        equipment_presentation: Any,
        preferences_provider: Callable[[], dict[str, Any]],
        save_preferences: Callable[[], None],
        refresh_roles: Callable[[], None],
        refresh_equipment: Callable[[], None],
    ) -> None:
        super().__init__(dialog_parent)
        self.app_context = app_context
        self.dialog_parent = dialog_parent
        self._equipment_presentation = equipment_presentation
        self._preferences_provider = preferences_provider
        self._save_preferences_callback = save_preferences
        self._refresh_roles_callback = refresh_roles
        self._refresh_equipment_callback = refresh_equipment
        self.btn_run: QPushButton | None = None
        self._worker: WorkerThread | None = None
        self.final_plan: dict = {}
        self.allocation_plan_diff: dict = {}
        self._allocation_dirty = False
        self._pending_allocation_snapshot_id: int | None = None
        self._allocation_lock_snapshot: AllocationLockSnapshot | None = None
        self._pending_archive_paths: list[Path] = []
        self._pending_strat = ""
        self._pending_sel: list[str] = []
        self._pending_cs: dict[str, Any] = {}
        self._pending_tape_main_filters: dict[str, Any] = {}
        self._pending_crit_priority_modes: dict[str, Any] = {}
        self._pending_set_effect_modes: dict[str, Any] = {}
        self._pending_priority_groups: Any = None
        self._pending_crit_rate_caps: dict[str, Any] = {}
        self._pending_custom_weapons: dict[str, Any] = {}
        self._allocation_custom_weapons: dict[str, Any] = {}
        self._ui_preferences: dict[str, Any] = {}

    def bind_run_button(self, button: QPushButton) -> None:
        self.btn_run = button

    def start(
        self,
        *,
        strategy: str,
        selected_roles: list[str],
        custom_sets: dict[str, Any],
        tape_main_filters: dict[str, Any],
        crit_priority_modes: dict[str, Any],
        set_effect_modes: dict[str, Any],
        priority_groups: Any,
        crit_rate_caps: dict[str, Any],
        custom_weapons: dict[str, Any],
    ) -> None:
        if self.btn_run is None:
            raise RuntimeError("allocation run button has not been bound")
        self._pending_strat = strategy
        self._pending_sel = selected_roles
        self._pending_cs = custom_sets
        self._pending_tape_main_filters = tape_main_filters
        self._pending_crit_priority_modes = crit_priority_modes
        self._pending_set_effect_modes = set_effect_modes
        self._pending_priority_groups = priority_groups
        self._pending_crit_rate_caps = crit_rate_caps
        self._pending_custom_weapons = custom_weapons
        _start_allocation_worker(self)

    def confirm_recompute(self) -> bool:
        self._ui_preferences = self._preferences_provider()
        return bool(_confirm_unsaved_allocation_before_recompute(self))

    def save(self, show_message: bool = True) -> bool:
        return self._save_alloc(show_message=show_message)

    def is_running(self) -> bool:
        return bool(self._worker is not None and self._worker.isRunning())

    def reset_account_state(self) -> None:
        self.final_plan = {}
        self.allocation_plan_diff = {}
        self._allocation_dirty = False
        self._pending_allocation_snapshot_id = None
        self._allocation_lock_snapshot = None
        self._equipment_presentation.clear()

    def _run_allocation(self, *args: Any, **kwargs: Any) -> Any:
        return _run_allocation(self, *args, **kwargs)

    def _on_done(self, result: Any) -> None:
        _on_done(self, result)

    def _on_exec_error(self, error: str) -> None:
        _on_exec_error(self, error)

    def _render_results(self, plan: dict) -> None:
        self._equipment_presentation.set_plan_context(
            final_plan=plan,
            plan_diff=self.allocation_plan_diff,
            snapshot_id=self._pending_allocation_snapshot_id,
            strategy=self._pending_strat,
            custom_weapons=self._pending_custom_weapons,
            locked_role_names=(
                self._allocation_lock_snapshot.locked_role_names
                if self._allocation_lock_snapshot is not None
                else ()
            ),
        )
        self._equipment_presentation.render(plan)

    def _save_alloc(self, show_message: bool = True) -> bool:
        self.allocation_plan_diff = dict(
            self._equipment_presentation.allocation_plan_diff or {}
        )
        return bool(_save_alloc(self, show_message=show_message))

    def _save_ui_preferences(self) -> None:
        self._save_preferences_callback()

    def _refresh_my_role(self) -> None:
        self._refresh_roles_callback()

    def _refresh_equip(self) -> None:
        self._refresh_equipment_callback()
