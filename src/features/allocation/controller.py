# 持有分配Worker、冻结请求、取消令牌与预览保存状态，线程执行留在runner。
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import threading
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QPushButton

from src.app.workers import WorkerThread
from src.domain.static_catalog import StaticCatalogRelease
from src.integrations.global_hotkeys import GlobalHotkeyManager
from src.services.allocation.filter_settings import AllocationFilterSettings
from src.services.allocation.locks import AllocationLockSnapshot
from src.features.allocation.runner import (
    _allocation_context_identity, _confirm_unsaved_allocation_before_recompute,
    _on_done, _on_exec_error, _run_allocation, _save_alloc, _start_allocation_worker,
)


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
        hotkey_manager: GlobalHotkeyManager,
    ) -> None:
        super().__init__(dialog_parent)
        self.app_context = app_context
        self.dialog_parent = dialog_parent
        self._equipment_presentation = equipment_presentation
        self._preferences_provider = preferences_provider
        self._save_preferences_callback = save_preferences
        self._refresh_roles_callback = refresh_roles
        self._refresh_equipment_callback = refresh_equipment
        self._hotkey_manager = hotkey_manager
        self._cancel_event = threading.Event()
        self._closing = False
        self.btn_run: QPushButton | None = None
        self._worker: WorkerThread | None = None
        self._save_worker: WorkerThread | None = None
        self._saving = False
        self.btn_save: QPushButton | None = None
        self.final_plan: dict = {}
        self._allocation_frozen_comparisons: dict = {}
        self.allocation_plan_diff: dict = {}
        self._allocation_dirty = False
        self._pending_allocation_snapshot_id: int | None = None
        self._pending_allocation_static_identity: tuple[Path, str, tuple[int, int]] | None = None
        self._pending_allocation_static_release: StaticCatalogRelease | None = None
        self._allocation_lock_snapshot: AllocationLockSnapshot | None = None
        self._selected_locked_role_names: frozenset[str] = frozenset()
        self._pending_archive_paths: list[Path] = []
        self._pending_axis_selection = None
        self._pending_strat = ""
        self._pending_sel: list[str] = []
        self._pending_cs: dict[str, Any] = {}
        self._pending_tape_main_filters: dict[str, Any] = {}
        self._pending_crit_priority_modes: dict[str, Any] = {}
        self._pending_set_effect_modes: dict[str, Any] = {}
        self._pending_priority_groups: Any = None
        self._pending_crit_rate_caps: dict[str, Any] = {}
        self._pending_crit_rate_baselines: dict[str, Any] = {}
        self._pending_custom_weapons: dict[str, Any] = {}
        self._pending_allocation_context_identity: tuple[int | None, str | None, Path] | None = None
        self._run_sequence = 0
        self._pending_run_id: int | None = None
        self._pending_filter_settings = AllocationFilterSettings()
        self._pending_blueprint_combo_limit = 2000
        self._allocation_custom_weapons: dict[str, Any] = {}
        self._ui_preferences: dict[str, Any] = {}

    def bind_run_button(self, button: QPushButton) -> None:
        self.btn_run = button

    def bind_save_button(self, button: QPushButton) -> None:
        self.btn_save = button

    def stop_save(self) -> None:
        self._cancel_event.set()  # 仅撤权；窗口门禁等待物理退场，不阻塞 GUI。

    def begin_close(self) -> None:
        self._closing = True
        self.cancel()

    def _on_worker_finished(self) -> None:
        worker = self.sender()
        if self._worker is worker:
            self._worker = None
        if worker is not None:
            worker.deleteLater()

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
        crit_rate_baselines: dict[str, Any],
        custom_weapons: dict[str, Any],
        filter_settings: AllocationFilterSettings,
        blueprint_combo_limit: int = 2000,
        axis_selection=None,
    ) -> None:
        if self._closing:
            raise RuntimeError("分配计算已关闭")
        if self.btn_run is None:
            raise RuntimeError("allocation run button has not been bound")
        if self.is_running():
            raise RuntimeError("已有分配计算任务正在运行")
        self._pending_axis_selection = axis_selection
        self._pending_strat = strategy
        self._pending_sel = selected_roles
        self._pending_cs = custom_sets
        self._pending_tape_main_filters = tape_main_filters
        self._pending_crit_priority_modes = crit_priority_modes
        self._pending_set_effect_modes = set_effect_modes
        self._pending_priority_groups = priority_groups
        self._pending_crit_rate_caps = crit_rate_caps
        self._pending_crit_rate_baselines = crit_rate_baselines
        self._pending_custom_weapons = custom_weapons
        filter_settings.validate()
        self._pending_filter_settings = filter_settings
        self._pending_blueprint_combo_limit = int(blueprint_combo_limit)
        if self._pending_blueprint_combo_limit < 1:
            raise ValueError("图纸组合数必须为正整数")
        self._pending_allocation_context_identity = _allocation_context_identity(self)
        self._run_sequence += 1
        self._pending_run_id = self._run_sequence
        self._cancel_event = threading.Event()
        self._hotkey_manager.start(owner="allocation", on_stop=self.cancel)
        _start_allocation_worker(self)

    def cancel(self) -> None:
        """Handle the shared F12 stop key at optimizer safe points."""

        self._cancel_event.set()

    def confirm_recompute(self) -> bool:
        self._ui_preferences = self._preferences_provider()
        return bool(_confirm_unsaved_allocation_before_recompute(self))

    def save(self, show_message: bool = True) -> bool:
        if self._closing:
            return False
        return self._save_alloc(show_message=show_message)

    def is_running(self) -> bool:
        return self._saving or any(
            worker is not None and worker.isRunning()
            for worker in (self._worker, self._save_worker)
        )

    def clear_preview(self) -> None:
        """Discard a displayed calculation without changing persisted plans or inputs."""

        self.final_plan = {}
        self._allocation_frozen_comparisons = {}
        self.allocation_plan_diff = {}
        self._allocation_dirty = False
        self._pending_allocation_snapshot_id = None
        self._pending_allocation_static_identity = None
        self._pending_allocation_static_release = None
        self._allocation_lock_snapshot = None
        self._selected_locked_role_names = frozenset()
        self._allocation_custom_weapons = {}
        self._equipment_presentation.clear()

    def reset_account_state(self) -> None:
        self.final_plan = {}
        self._allocation_frozen_comparisons = {}
        self.allocation_plan_diff = {}
        self._allocation_dirty = False
        self._pending_allocation_snapshot_id = None
        self._pending_allocation_static_identity = None
        self._pending_allocation_static_release = None
        self._allocation_lock_snapshot = None
        self._selected_locked_role_names = frozenset()
        self._pending_allocation_context_identity = None
        self._pending_run_id = None
        self._pending_filter_settings = AllocationFilterSettings()
        self._cancel_event.set()
        self._hotkey_manager.stop(owner="allocation")
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
            locked_role_names=self._selected_locked_role_names,
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
