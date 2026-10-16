# 持有计算页面、扫描工作线程与冻结的任务状态。
"""Scanning page controller and its composed allocation collaborators."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog, QMessageBox, QPushButton, QWidget

from src.app.context import AppContext
from src.domain.work_mode import WorkMode
from src.features.allocation.controller import AllocationController
from src.features.allocation.filter_settings_dialog import AllocationFilterSettingsDialog
from src.integrations.global_hotkeys import GlobalHotkeyManager
from src.ui.equipment_presentation import EquipmentPresentation
from src.services.allocation.filter_settings import (
    AllocationFilterSettings,
    AllocationFilterSettingsService,
    AllocationFilterValidationError,
)
from src.utils.logger import logger
from src.features.scanning.file_workflow import (
    clear_capture_recovery, update_capture_actions,
    resume_capture_stage, retry_capture_publication,
    publication_retry_done, publication_retry_failed,
    delete_paths as _delete_paths,
    matching_scope_files as _matching_scope_files,
    move_first_full_scan_to_tail as _move_first_full_scan_to_tail,
    move_to_failed as _move_to_failed,
    next_full_scan_index as _next_full_scan_index,
    postprocess_vision_files as _postprocess_vision_files,
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
from src.features.scanning.entry_controls import open_scan_post_action_manager as _open_scan_post_action_manager
from src.features.scanning.vision_commit_controller import VisionCommitController
from src.features.scanning.manual_recovery import ManualRecoverySession
from src.features.scanning.dependencies import scanning_dependencies_are_current
from functools import partial
from src.features.scanning.workflow import (
    _page_execute,
    _on_scan_change,
    _on_priority_changed,
    _do_exec,
    _start_vision_processing,
    _on_vision_progress,
    _on_vision_done,
    _on_vision_committed,
    _on_vision_commit_failed,
    _on_vision_application_failed,
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

    resume_capture_stage = resume_capture_stage
    retry_capture_publication = retry_capture_publication
    _on_publication_retry_done = publication_retry_done
    _on_publication_retry_failed = publication_retry_failed
    _on_scan_change = _on_scan_change
    _on_priority_changed = _on_priority_changed
    _open_scan_post_action_manager = _open_scan_post_action_manager
    _scan_lifecycle = _scan_lifecycle
    _is_scope_image = _is_scope_image
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
    _on_vision_committed = _on_vision_committed
    _on_vision_commit_failed = _on_vision_commit_failed
    _on_vision_application_failed = _on_vision_application_failed
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
        role_suit_matching=None,
        dialog_parent: QWidget,
        navigate: Callable[[str], None],
        work_mode_provider: Callable[[], WorkMode],
        minimize_window: Callable[[], None],
        restore_window: Callable[[], None],
        activate_window: Callable[[], None],
        update_inventory_status: Callable[[], None],
        refresh_home: Callable[[], None],
        preferences_provider: Callable[[], dict[str, Any]],
        save_preferences: Callable[[], None],
        global_preferences,
        refresh_roles: Callable[[], None],
        refresh_equipment: Callable[[], None],
        card_factory: Callable[..., Any],
        equipment_presentation: EquipmentPresentation,
        hotkey_manager: GlobalHotkeyManager,
        operation_guard: Callable[[str], None] | None = None,
        operation_generation: Callable[[], object] | None = None,
        operation_entry: Callable[[str, str], bool] | None = None,
        operation_unavailable: Callable[[str, str, str], None] | None = None,
        prepare_calculation: Callable[[Callable[[], None]], None] | None = None,
        axis_selection_service_provider: Callable[[], Any] | None = None,
    ) -> None:
        super().__init__(dialog_parent)
        self.app_context = app_context
        self.role_suit_matching = role_suit_matching
        self.operation_entry = operation_entry
        self.operation_unavailable = operation_unavailable
        self.operation_guard = operation_guard
        self.operation_generation = operation_generation
        self._prepare_calculation = prepare_calculation
        self._axis_selection_service_provider = axis_selection_service_provider
        self.dialog_parent = dialog_parent
        self.navigate = navigate
        self.work_mode_provider = work_mode_provider
        self._minimize_window = minimize_window
        self._restore_window = restore_window
        self._activate_window = activate_window
        self._update_inventory_status_callback = update_inventory_status
        self._refresh_home_callback = refresh_home
        self._card_factory = card_factory
        self._preferences_provider = preferences_provider
        self._save_preferences_callback = save_preferences
        from src.services.preferences.data_reading_settings import DataReadingSettings
        self._data_reading_settings = DataReadingSettings(global_preferences)
        self._equipment_presentation = equipment_presentation
        self._hotkey_manager = hotkey_manager
        self._page: QWidget | None = None
        self._manual_recovery = ManualRecoverySession()
        self._capture_recovery = None
        self._vision_commit = VisionCommitController(partial(scanning_dependencies_are_current, self), self)
        self._vision_commit.committed.connect(self._on_vision_committed)
        self._vision_commit.failed.connect(self._on_vision_commit_failed)
        self._vision_commit.application_failed.connect(self._on_vision_application_failed)
        self._vision_commit.publication_retried.connect(self._on_publication_retry_done)
        self._vision_commit.publication_retry_failed.connect(self._on_publication_retry_failed)
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
            hotkey_manager=hotkey_manager,
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
        self._allocation_controller.bind_save_button(self.btn_save)
        update_capture_actions(self)
        return self._page

    def is_running(self) -> bool:
        return self._vision_commit.is_running() or _scanning_is_running(self) or self._allocation_controller.is_running()

    def _do_exec(self):
        if self.is_running():
            return
        if self._prepare_calculation is None:
            _do_exec(self)
        else:
            self._prepare_calculation(lambda: _do_exec(self))

    def choose_axis_allocation(self, selected_roles, *, initial_request=None):
        from src.features.allocation.axis_selection_dialog import AxisAllocationSelectionDialog
        if self._axis_selection_service_provider is None:
            QMessageBox.warning(self.dialog_parent, "轴优化未就绪", "当前账号轴选择服务未就绪。")
            return None
        service = self._axis_selection_service_provider()
        try:
            choices = service.choices(selected_roles)
        except (ValueError, RuntimeError) as error:
            QMessageBox.warning(self.dialog_parent, "轴优化未就绪", str(error))
            return None
        dialog = AxisAllocationSelectionDialog(service=service, choices=choices, initial_request=initial_request,
                                                parent=self.dialog_parent)
        try:
            return dialog.selection if dialog.exec() == QDialog.Accepted else None
        finally:
            dialog.close()
            dialog.deleteLater()

    def start_axis_allocation_from_battle(self, request):
        """明确报告来自边际页；走同一冻结服务，不模拟点击或持久化候选。"""
        if self.is_running():
            QMessageBox.warning(self.dialog_parent, "计算进行中", "请先停止当前计算或扫描。")
            return
        self.build_page()
        self.navigate("execute")
        self.scan_group.button(4).setChecked(True)
        self.strategy_group.button(3).setChecked(True)
        if not self.role_selector.get_selected():
            QMessageBox.information(self.dialog_parent, "请选择计算成员", "请先在计算页明确选择角色和优先组，再按本轴优化。")
            return
        def start():
            _do_exec(self, axis_initial_request=request)
        if self._prepare_calculation is None:
            start()
        else:
            self._prepare_calculation(start)

    def request_stop(self) -> None:
        """Revoke active input/parse work without waiting on the GUI thread."""
        self._manual_recovery.cancel()
        self._vision_commit.cancel()
        clear_capture_recovery(self)
        self._allocation_controller.cancel()
        self._stop_scan_hotkeys()
        for name in ("_scan_worker", "_gamepad_worker"):
            worker = getattr(self, name, None)
            if worker is not None and hasattr(worker, "request_cancel"):
                worker.request_cancel()
                continue
            scanner = getattr(worker, "scanner", None) if worker is not None else None
            if scanner is not None:
                if hasattr(scanner, "emergency_stop"):
                    scanner.emergency_stop()
        vision_worker = getattr(self, "_vision_worker", None)
        # 已结束线程的 processing_done 仍可能排队；停止必须一并撤销迟到交付。
        if vision_worker is not None and hasattr(vision_worker, "request_cancel"):
            vision_worker.request_cancel()

    def stop(self) -> None:
        """Stop account-bound workers, retaining the existing shutdown deadline."""
        self.request_stop()
        self._allocation_controller.stop_save()
        for worker in (self._scan_worker, self._gamepad_worker, self._vision_worker):
            if worker is not None and worker.isRunning():
                worker.wait(5000)

    def begin_close(self) -> None:
        """退出先撤销副作用与交付；保留 owner，物理等待由窗口门禁负责。"""
        self._manual_recovery.close()
        self._vision_commit.close()
        self._allocation_controller.begin_close()
        self.request_stop()

    def close(self) -> None:
        self.begin_close()
        self.stop()

    def reset_account_state(self) -> None:
        self.request_stop()
        self._vision_commit.reset_account_state()
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

    def load_reading_options_expanded(self) -> bool:
        try:
            return self._data_reading_settings.load()
        except (OSError, ValueError):
            QMessageBox.warning(self.dialog_parent, "数据读取", "展开状态读取失败，本次默认收起；原设置未修改。")
            return False

    def save_reading_options_expanded(self, expanded: bool) -> bool:
        try:
            self._data_reading_settings.save(expanded)
        except (OSError, ValueError):
            QMessageBox.warning(self.dialog_parent, "数据读取", "展开状态保存失败，已保留原状态；请检查配置文件。")
            return False
        return True

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

    def clear_calculation(self) -> None:
        """Discard only the current allocation preview, not saved plans or inputs."""

        if self.is_running():
            QMessageBox.information(self.dialog_parent, "暂不能清空", "请等待当前任务结束后再清空计算结果。")
            return
        answer = QMessageBox.question(
            self.dialog_parent,
            "清空计算结果",
            "清空当前计算结果？\n已保存的配装、角色选择和分配设置不会改变。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._allocation_controller.clear_preview()
