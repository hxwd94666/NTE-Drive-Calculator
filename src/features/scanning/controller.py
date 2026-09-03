# 持有计算页面、扫描工作线程与冻结的任务状态。
"""Scanning page controller and its composed allocation collaborators."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog, QMessageBox, QPushButton, QWidget

from src.app.context import AppContext
from src.features.allocation.runner import AllocationController
from src.features.allocation.filter_settings_dialog import AllocationFilterSettingsDialog
from src.integrations.global_hotkeys import GlobalHotkeyManager
from src.ui.equipment_presentation import EquipmentPresentation
from src.services.allocation_filter_settings import (
    AllocationFilterSettings,
    AllocationFilterSettingsService,
    AllocationFilterValidationError,
)
from src.utils.logger import logger
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
from src.features.scanning.hotkey_actions import (
    on_hotkey_capture as _on_hk_capture,
    on_hotkey_finish as _on_hk_finish,
    on_hotkey_stop as _on_hk_stop,
)
from src.features.scanning.scan_contracts import scanning_is_running as _scanning_is_running
from src.features.scanning.workflow import (
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
        equipment_presentation: EquipmentPresentation,
        hotkey_manager: GlobalHotkeyManager,
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
        self._equipment_presentation = equipment_presentation
        self._hotkey_manager = hotkey_manager
        self._page: QWidget | None = None
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
        self._pending_crit_rate_baselines: dict[str, Any] = {}
        self._pending_set_effect_modes: dict[str, Any] = {}
        self._pending_priority_groups: Any = None
        self._pending_filter_settings = AllocationFilterSettings()
        self._allocation_filter_settings = AllocationFilterSettings()
        self._ui_preferences: dict[str, Any] = {}
        self._allocation_controller = AllocationController(
            app_context=app_context,
            dialog_parent=dialog_parent,
            equipment_presentation=equipment_presentation,
            preferences_provider=preferences_provider,
            save_preferences=save_preferences,
            refresh_roles=refresh_roles,
            refresh_equipment=refresh_equipment,
        )

    def build_page(self) -> QWidget:
        if self._page is not None:
            return self._page
        self._ui_preferences = self._preferences_provider()
        self._reload_allocation_filter_settings()
        self._page = _page_execute(self)
        self._equipment_presentation.bind_widgets(
            result_card=self.result_card,
            result_content_layout=self.result_content_layout,
            role_selector=self.role_selector,
        )
        self._allocation_controller.bind_run_button(self.btn_run)
        return self._page

    def is_running(self) -> bool:
        return _scanning_is_running(self) or self._allocation_controller.is_running()

    def stop(self) -> None:
        """Stop account-bound capture/parse workers and release held input."""

        self._stop_scan_hotkeys()
        for name in ("_scan_worker", "_gamepad_worker"):
            worker = getattr(self, name, None)
            scanner = getattr(worker, "scanner", None) if worker is not None else None
            if scanner is not None:
                if hasattr(scanner, "emergency_stop"):
                    scanner.emergency_stop()
                else:
                    scanner._stopped = True
            if worker is not None and worker.isRunning():
                worker.wait(5000)
        vision_worker = getattr(self, "_vision_worker", None)
        if vision_worker is not None and vision_worker.isRunning():
            if hasattr(vision_worker, "request_cancel"):
                vision_worker.request_cancel()
            vision_worker.wait(5000)

    def close(self) -> None:
        self.stop()

    def reset_account_state(self) -> None:
        self._pending_archive_paths = []
        self._pending_parse_only = False
        self._pending_parse_scope = "all"
        self._pending_scan_mode = None
        self._scan_dependencies = None
        self._ui_preferences = self._preferences_provider()
        self._reload_full_scan_preference_widgets()
        self._reload_allocation_filter_settings()
        self._allocation_controller.reset_account_state()

    def _reload_allocation_filter_settings(self) -> None:
        service = AllocationFilterSettingsService(
            self.app_context.account.user_database_path
        )
        try:
            self._allocation_filter_settings = service.load()
        except AllocationFilterValidationError as exc:
            self._allocation_filter_settings = AllocationFilterSettings()
            logger.warning(f"账号分配过滤设置无效，已使用默认值: {exc}")
        self._update_allocation_filter_summary()

    def _open_allocation_filter_settings(self) -> None:
        dialog = AllocationFilterSettingsDialog(
            self._allocation_filter_settings,
            self.dialog_parent,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        settings = dialog.settings()
        try:
            AllocationFilterSettingsService(
                self.app_context.account.user_database_path
            ).save(settings)
        except Exception as exc:
            QMessageBox.warning(
                self.dialog_parent,
                "保存过滤设置失败",
                str(exc),
            )
            return
        self._allocation_filter_settings = settings
        self._update_allocation_filter_summary()

    def _allocation_filter_settings_are_valid(self) -> bool:
        try:
            self._allocation_filter_settings.validate()
        except AllocationFilterValidationError as exc:
            QMessageBox.warning(
                self.dialog_parent,
                "过滤设置无效",
                str(exc),
            )
            return False
        return True

    def _update_allocation_filter_summary(self) -> None:
        label = getattr(self, "allocation_filter_summary", None)
        if label is None:
            return
        quality_names = {"Blue": "蓝色", "Purple": "紫色", "Gold": "金色"}
        type_names = {"tape": "卡带", "drive": "驱动"}
        qualities = "、".join(
            quality_names[value]
            for value in ("Blue", "Purple", "Gold")
            if value in self._allocation_filter_settings.qualities
        ) or "未选择"
        item_types = "、".join(
            type_names[value]
            for value in ("tape", "drive")
            if value in self._allocation_filter_settings.item_types
        ) or "未选择"
        label.setText(f"过滤设置：品质 {qualities}；类型 {item_types}")

    def _start_scan_hotkeys(self, mode: str) -> None:
        """Bind this scan session without exposing hotkeys to other features."""

        manual_capture = mode == "semi"
        self._hotkey_manager.start(
            owner="scanning",
            on_stop=self._on_hk_stop,
            on_capture=self._on_hk_capture if manual_capture else None,
            on_finish=self._on_hk_finish if manual_capture else None,
        )

    def _stop_scan_hotkeys(self) -> None:
        self._hotkey_manager.stop(owner="scanning")

    def _card(self, *args: Any, **kwargs: Any) -> Any:
        return self._card_factory(*args, **kwargs)

    def _save_ui_preferences(self) -> None:
        self._save_preferences_callback()

    def _reload_full_scan_preference_widgets(self) -> None:
        """Project current-account scan preferences onto an already-built page."""
        preferences = self._ui_preferences
        capture_driver = str(
            preferences.get("full_scan_capture_driver", "mouse")
        ).strip().casefold()
        if capture_driver not in {"mouse", "gamepad"}:
            capture_driver = "mouse"
        group = getattr(self, "full_scan_driver_group", None)
        if group is not None:
            for button in group.buttons():
                button.blockSignals(True)
                button.setChecked(button.property("capture_driver") == capture_driver)
                button.blockSignals(False)
        dual_thread = getattr(self, "scan_dual_thread_check", None)
        if dual_thread is not None:
            dual_thread.blockSignals(True)
            dual_thread.setChecked(
                bool(preferences.get("full_scan_dual_thread_processing", True))
            )
            dual_thread.blockSignals(False)
        compatibility = getattr(self, "scan_amd_compat_check", None)
        if compatibility is not None:
            compatibility.blockSignals(True)
            compatibility.setChecked(
                bool(preferences.get("full_scan_amd_compatibility", False))
            )
            compatibility.blockSignals(False)

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
            crit_rate_baselines=self._pending_crit_rate_baselines,
            custom_weapons=self._pending_custom_weapons,
            filter_settings=self._pending_filter_settings,
        )

    def _save_alloc(self, show_message: bool = True) -> bool:
        return self._allocation_controller.save(show_message=show_message)
