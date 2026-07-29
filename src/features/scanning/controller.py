# 持有计算页面、扫描工作线程与冻结的任务状态。
"""Scanning page controller and its composed allocation collaborators."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QPushButton, QWidget

from src.app.context import AppContext
from src.features.allocation.results_view import AllocationResultsView
from src.features.allocation.runner import AllocationController
from src.features.identification.controller import IdentificationController
from src.optimizer.scoring import ScoringEngine
from src.features.scanning.file_workflow import (
    delete_paths as _delete_paths,
    matching_scope_files as _matching_scope_files,
    move_first_full_scan_to_tail as _move_first_full_scan_to_tail,
    move_to_failed as _move_to_failed,
    next_full_scan_index as _next_full_scan_index,
    postprocess_vision_files as _postprocess_vision_files,
    prepare_incremental_parse as _prepare_incremental_parse,
    rename_incremental_successes as _rename_incremental_successes,
    scan_lifecycle as _scan_lifecycle,
    scope_image as _is_scope_image,
    unique_path as _unique_path,
)
from src.features.scanning.hotkeys import (
    hotkey_poll_loop as _hotkey_poll_loop,
    hotkey_to_vk as _hotkey_to_vk,
    on_hotkey_capture as _on_hk_capture,
    on_hotkey_finish as _on_hk_finish,
    on_hotkey_stop as _on_hk_stop,
    register_scan_hotkeys as _register_scan_hotkeys,
    unregister_scan_hotkeys as _unregister_scan_hotkeys,
    win_hotkey_loop as _win_hotkey_loop,
)
from src.features.scanning.workflow import (
    _scanning_is_running,
    _page_execute,
    _on_scan_change,
    _on_priority_changed,
    _open_scan_post_action_manager,
    _do_exec,
    _start_vision_processing,
    _on_vision_progress,
    _on_vision_done,
    _on_vision_error,
    _on_vision_cancel,
    _on_vision_canceled,
    _start_scan,
    _start_gamepad_scan,
    _on_gamepad_scan_done,
    _on_gamepad_parse_progress,
    _on_gamepad_parse_done,
    _on_gamepad_post_actions_ready,
    _on_gamepad_error,
    _on_gamepad_pipeline_done,
    _on_scan_done,
    _on_scan_error,
)

__all__ = ["ScanningController"]

class ScanningController(QObject):
    """Own the calculation page, scanning workers, and their frozen state."""

    result_card: Any
    result_content_layout: Any
    role_selector: Any
    btn_run: QPushButton

    _on_scan_change = _on_scan_change
    _on_priority_changed = _on_priority_changed
    _open_scan_post_action_manager = _open_scan_post_action_manager
    _do_exec = _do_exec
    _scan_lifecycle = _scan_lifecycle
    _is_scope_image = _is_scope_image
    _prepare_incremental_parse = _prepare_incremental_parse
    _matching_scope_files = _matching_scope_files
    _unique_path = _unique_path
    _move_to_failed = _move_to_failed
    _delete_paths = _delete_paths
    _next_full_scan_index = _next_full_scan_index
    _rename_incremental_successes = _rename_incremental_successes
    _move_first_full_scan_to_tail = _move_first_full_scan_to_tail
    _postprocess_vision_files = _postprocess_vision_files
    _start_vision_processing = _start_vision_processing
    _on_vision_progress = _on_vision_progress
    _on_vision_done = _on_vision_done
    _on_vision_error = _on_vision_error
    _on_vision_cancel = _on_vision_cancel
    _on_vision_canceled = _on_vision_canceled
    _start_scan = _start_scan
    _start_gamepad_scan = _start_gamepad_scan
    _on_gamepad_scan_done = _on_gamepad_scan_done
    _on_gamepad_parse_progress = _on_gamepad_parse_progress
    _on_gamepad_parse_done = _on_gamepad_parse_done
    _on_gamepad_post_actions_ready = _on_gamepad_post_actions_ready
    _on_gamepad_error = _on_gamepad_error
    _on_gamepad_pipeline_done = _on_gamepad_pipeline_done
    _on_scan_done = _on_scan_done
    _on_scan_error = _on_scan_error
    _register_scan_hotkeys = _register_scan_hotkeys
    _hotkey_to_vk = _hotkey_to_vk
    _win_hotkey_loop = _win_hotkey_loop
    _hotkey_poll_loop = _hotkey_poll_loop
    _unregister_scan_hotkeys = _unregister_scan_hotkeys
    _on_hk_stop = _on_hk_stop
    _on_hk_capture = _on_hk_capture
    _on_hk_finish = _on_hk_finish

    def __init__(
        self,
        *,
        app_context: AppContext,
        dialog_parent: QWidget,
        minimize_window: Callable[[], None],
        restore_window: Callable[[], None],
        activate_window: Callable[[], None],
        update_inventory_status: Callable[[], None],
        refresh_home: Callable[[], None],
        preferences_provider: Callable[[], dict[str, Any]],
        save_preferences: Callable[[], None],
        refresh_roles: Callable[[], None],
        refresh_equipment: Callable[[], None],
        card_factory: Callable[..., Any],
        capture_hotkey: str,
        finish_hotkey: str,
        stop_hotkey: str,
    ) -> None:
        super().__init__(dialog_parent)
        self.app_context = app_context
        self.dialog_parent = dialog_parent
        self._minimize_window = minimize_window
        self._restore_window = restore_window
        self._activate_window = activate_window
        self._update_inventory_status_callback = update_inventory_status
        self._refresh_home_callback = refresh_home
        self._card_factory = card_factory
        self._preferences_provider = preferences_provider
        self._save_preferences_callback = save_preferences
        self._page: QWidget | None = None
        self.identification_controller: IdentificationController | None = None
        self._hk_capture = capture_hotkey
        self._hk_finish = finish_hotkey
        self._hk_stop = stop_hotkey
        self._hk_active = False
        self._hk_mode: str | None = None
        self._hk_thread_id: int | None = None
        self._scan_worker: Any = None
        self._gamepad_worker: Any = None
        self._vision_worker: Any = None
        self._progress_dlg: Any = None
        self._scan_dependencies: Any = None
        self._scan_operation_active = False
        self._pending_archive_paths: list[Path] = []
        self._pending_delete_after_parse: list[Path] = []
        self._pending_parse_only = False
        self._pending_parse_scope = "all"
        self._pending_scan_mode: str | None = None
        self._pending_probe_duplicate_count = 0
        self._replace_inventory_on_next_parse = False
        self._gamepad_pipeline_finished = False
        self._gamepad_post_actions_enabled = False
        self._gamepad_suppress_parse_ui = False
        self._gamepad_parse_progress = (0, 0, "")
        self._pending_strat = ""
        self._pending_sel: list[str] = []
        self._pending_cs: dict[str, Any] = {}
        self._pending_custom_weapons: dict[str, Any] = {}
        self._pending_tape_main_filters: dict[str, Any] = {}
        self._pending_crit_priority_modes: dict[str, Any] = {}
        self._pending_crit_rate_caps: dict[str, Any] = {}
        self._pending_set_effect_modes: dict[str, Any] = {}
        self._pending_priority_groups: Any = None
        self._ui_preferences: dict[str, Any] = {}
        self.results_view = AllocationResultsView(
            app_context=app_context,
            dialog_parent=dialog_parent,
        )
        self._allocation_controller = AllocationController(
            app_context=app_context,
            dialog_parent=dialog_parent,
            results_view=self.results_view,
            preferences_provider=preferences_provider,
            save_preferences=save_preferences,
            refresh_roles=refresh_roles,
            refresh_equipment=refresh_equipment,
        )

    def build_page(self) -> QWidget:
        if self._page is not None:
            return self._page
        self._ui_preferences = self._preferences_provider()
        self._page = _page_execute(self)
        self.results_view.bind_widgets(
            result_card=self.result_card,
            result_content_layout=self.result_content_layout,
            role_selector=self.role_selector,
        )
        self._allocation_controller.bind_run_button(self.btn_run)
        return self._page

    def set_identification_controller(
        self,
        controller: IdentificationController,
    ) -> None:
        self.identification_controller = controller

    def update_hotkeys(
        self,
        *,
        capture_hotkey: str,
        finish_hotkey: str,
        stop_hotkey: str,
    ) -> None:
        self._hk_capture = capture_hotkey
        self._hk_finish = finish_hotkey
        self._hk_stop = stop_hotkey

    def update_catalog(
        self,
        *,
        roles_db: dict[str, Any],
        scoring_engine: ScoringEngine,
        shape_areas: dict[str, int],
    ) -> None:
        self.results_view.update_catalog(
            roles_db=roles_db,
            scoring_engine=scoring_engine,
            shape_areas=shape_areas,
        )

    def is_running(self) -> bool:
        return _scanning_is_running(self) or self._allocation_controller.is_running()

    def reset_account_state(self) -> None:
        self._pending_archive_paths = []
        self._pending_parse_only = False
        self._pending_parse_scope = "all"
        self._pending_scan_mode = None
        self._scan_dependencies = None
        self._allocation_controller.reset_account_state()

    def register_hotkeys(self, mode: str) -> None:
        self._register_scan_hotkeys(mode)

    def unregister_hotkeys(self) -> None:
        self._unregister_scan_hotkeys()

    def equipment_card(self, *args: Any, **kwargs: Any) -> Any:
        return self.results_view.equipment_card(*args, **kwargs)

    @property
    def equipment_presentation(self) -> AllocationResultsView:
        """Expose the shared, public equipment presentation boundary."""

        return self.results_view

    def _card(self, *args: Any, **kwargs: Any) -> Any:
        return self._card_factory(*args, **kwargs)

    def _save_ui_preferences(self) -> None:
        self._save_preferences_callback()

    def showMinimized(self) -> None:
        self._minimize_window()

    def showNormal(self) -> None:
        self._restore_window()

    def activateWindow(self) -> None:
        self._activate_window()

    def _update_inventory_status(self) -> None:
        self._update_inventory_status_callback()

    def _refresh_home(self) -> None:
        self._refresh_home_callback()

    def _confirm_unsaved_allocation_before_recompute(self) -> bool:
        return self._allocation_controller.confirm_recompute()

    def _start_allocation_worker(self) -> None:
        self._allocation_controller.start(
            strategy=self._pending_strat,
            selected_roles=self._pending_sel,
            custom_sets=self._pending_cs,
            tape_main_filters=self._pending_tape_main_filters,
            crit_priority_modes=self._pending_crit_priority_modes,
            set_effect_modes=self._pending_set_effect_modes,
            priority_groups=self._pending_priority_groups,
            crit_rate_caps=self._pending_crit_rate_caps,
            custom_weapons=self._pending_custom_weapons,
        )

    def _save_alloc(self, show_message: bool = True) -> bool:
        return self._allocation_controller.save(show_message=show_message)
