# 将倒带方案保存与实际执行绑定到工具页对话框。
"""UI mixin for saving and executing a rewind plan."""

from __future__ import annotations

from src.domain.localization.catalog import localized_text
from src.ui.common.enum_text import ui_enum_text
from src.ui.common.text_catalog import plain_tooltip, resolve_text_catalog
from concurrent.futures import CancelledError
from copy import deepcopy
from src.features.input_operation_entry import request_input_entry, show_input_unavailable

import threading
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from src.app.page_tasks import PageCommitLane
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.rewind.dual_target import RewindSourceChanged
from src.features.drive_assembly.input_backends import PyAutoGuiMouseBackend
from src.integrations.runtime.operation_guard import bind_execution_guard
from src.features.drive_assembly.rewind.execution import (
    RewindExecutionRequest,
    close_rewind_input_backend,
    execute_rewind_request,
)
from src.features.toolbox.rewind.execution_dialog import RewindExecutionDialog
from src.ui.common.text_catalog import set_button_text


class RewindExecutionUiMixin:
    _rewind_foreground_settle_seconds = 1.0
    _rewind_hotkey_owner = "rewind_execution"

    def _rewind_execution_state(self):
        # 嵌套确认会运行 Qt 事件；确认绑定本次策略、方案和选项，不自动授权新草稿。
        target_matches = self._strategy_key != 'dual_target' or self._dual_request_matches_saved()
        return deepcopy((self._strategy_key, self._preferences, self._saved_rewind_shape_ids,
                         self._rewind_options, target_matches))

    def _source_change_lines(self, changes):
        # 库存在前，角色按正式 ID 稳定排序；显示名只作展示，不参与许可判断。
        text, names = self.text_catalog.render, getattr(self, '_role_names', {})
        ordered = sorted(changes, key=lambda row: (row.kind != 'inventory', row.character_id or 0, row.kind))
        return '\n'.join(text('rewind.execution.source_changed.inventory') if row.character_id is None else
                         text('rewind.execution.source_changed.' + row.kind,
                              name=names.get(row.character_id, str(row.character_id))) for row in ordered)

    def _confirm_rewind_source_change(self, changes):
        text = self.text_catalog.render
        message = QMessageBox(self)
        message.setWindowTitle(text('rewind.execution.source_changed.title'))
        message.setIcon(QMessageBox.Warning)
        message.setTextFormat(Qt.PlainText)
        message.setText(text('rewind.execution.source_changed.body', changes=self._source_change_lines(changes)))
        message.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        set_button_text(message.button(QMessageBox.Ok), text('common.confirm'))
        set_button_text(message.button(QMessageBox.Cancel), text('common.cancel'))
        message.setDefaultButton(QMessageBox.Cancel)
        message.setEscapeButton(QMessageBox.Cancel)
        message.adjustSize()
        fit_dialog_to_available_screen(message)
        QApplication.beep()
        try:
            return message.exec() == QMessageBox.Ok
        finally:
            message.deleteLater()

    def _check_saved_plan(self):
        """返回本次冻结来源与已确认的变化集合；取消或静态等硬门禁失败时不产生执行许可。"""
        guard = deepcopy(self._preferences.get('saved_guard'))
        if len(self._saved_rewind_shape_ids) != 8 or not guard:
            return None, frozenset()
        initial = self._rewind_execution_state()
        accepted = frozenset()
        try:
            if self._strategy_key == 'dual_target' and self._preferences.get('plan_origin') == 'dual_target' and not self._dual_request_matches_saved():
                raise ValueError(self.text_catalog.render('rewind.dual.target_changed'))
            try:
                self._service.validate_dual_target_guard(guard, checkpoint=self._check_selection_context)
            except RewindSourceChanged as changed:
                if not self._confirm_rewind_source_change(changed.changes):
                    return None
                self._check_selection_context()
                if self._rewind_execution_state() != initial:
                    raise ValueError(self.text_catalog.render('rewind.execution.plan_changed'))
                try:
                    self._service.validate_dual_target_guard(guard, checkpoint=self._check_selection_context,
                                                           accepted_changes=changed.changes)
                except RewindSourceChanged as later:
                    # 确认期间又出现未展示的变化：本次许可不覆盖它，要求用户重新发起。
                    raise ValueError(self.text_catalog.render('rewind.execution.plan_changed')) from later
                accepted = changed.changes
        except CancelledError:
            return None
        except Exception as error:
            QMessageBox.warning(self, self.text_catalog.render('rewind.dual.stale_plan'), self._preference_error_text(error))
            return None
        # 确认只在调用栈与本次 worker 内传递，不落库、不存为长期忽略标记。
        return guard, accepted

    def _save_plan(self) -> None:
        _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
        checker = getattr(self, "_check_selection_context", None)
        if checker is not None:
            try:
                checker()
            except CancelledError:
                return
        if getattr(self, "_recommendation_invalidated", False):
            QMessageBox.warning(self, _ui_text('rewind.execution.save_plan.qmessagebox'), plain_tooltip(_ui_text('rewind.execution.save_plan.qmessagebox.body')))
            return
        # 保存直接覆盖本策略八槽，并原样记录生成时的冻结来源；库存或所选配装此后更新不阻止保存。
        analysis = getattr(self, "_generated_analysis", None)
        if not self._slots_complete():
            return
        shape_ids = [slot.shape.shape_id for slot in self._editable_slots if slot is not None]
        saver = getattr(self._service, "save_preferences", None)
        if self._strategy_key == 'dual_target':
            try:
                self._save_dual_pool(analysis, shape_ids)
            except Exception as error:
                QMessageBox.warning(self, self.text_catalog.render('rewind.dual.save_error'), self._preference_error_text(error))
                return
        elif callable(saver):
            preferences = self._capture_preferences()
            preferences["saved_rewind_shape_ids"] = shape_ids
            preferences["saved_rewind_slots"] = self._serialize_rewind_slots()
            preferences['plan_origin'] = ('manual' if self._manual_plan_draft else
                                           self._preferences.get('plan_origin', 'manual'))
            try:
                updated = saver(preferences, save_plan=True, analysis=analysis,
                                checkpoint=self._check_selection_context)
                self._remember_preferences(updated)
            except Exception as error:
                QMessageBox.warning(self, _ui_text('rewind.execution.save_plan.qmessagebox_3'), plain_tooltip(_ui_text('rewind.execution.save_plan.qmessagebox.state', error=self._preference_error_text(error))))
                return
        self._saved_rewind_shape_ids = tuple(shape_ids)
        self._saved_rewind_slots = tuple(self._serialize_rewind_slots())
        self._save_plan_button.setText(_ui_text('rewind.execution.save_plan.save_plan_button'))

    def _configure_rewind(self) -> None:
        _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
        if not self._rewind_context_current():
            return
        if not request_input_entry(self, "interface_input", "游戏内倒带"):
            return
        if not self._rewind_context_current():
            return
        dialog = RewindExecutionDialog(self, initial=self._rewind_options, text_catalog=getattr(self, 'text_catalog', None))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        # 确认框与偏好错误提示均可运行账号续体，旧草稿不能在新代次重新获权。
        if not self._rewind_context_current():
            return
        self._rewind_options = dialog.options()
        self._save_preferences()
        if not self._rewind_context_current():
            return
        if self._rewind_options.drive_customization == "apply_plan" and len(self._saved_rewind_shape_ids) != 8:
            self._start_rewind_button.setText(_ui_text('rewind.execution.configure_rewind.start_rewind_button'))
            return
        self._start_rewind_execution()

    def _start_rewind_execution(self) -> None:
        _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
        if not self._rewind_context_current() or self.is_rewind_running():
            return
        if not request_input_entry(self, "interface_input", "游戏内倒带"):
            return
        if not self._rewind_context_current() or self.is_rewind_running():
            return
        saved_guard, accepted_changes = None, frozenset()
        if self._rewind_options.drive_customization == 'apply_plan':
            approval = self._check_saved_plan()
            if approval is None:
                return
            saved_guard, accepted_changes = approval
        if not self._rewind_context_current() or self.is_rewind_running():
            return
        request = RewindExecutionRequest(
            qualities=self._rewind_options.qualities,
            drive_customization=self._rewind_options.drive_customization,
            shape_ids=self._saved_rewind_shape_ids,
            ten_limits=self._rewind_options.ten_limits,
        )
        hotkey_manager = self._rewind_hotkey_manager()
        active_owner = getattr(hotkey_manager, "active_owner", None)
        if active_owner not in (None, self._rewind_hotkey_owner):
            QMessageBox.information(
                self,
                _ui_text('rewind.execution.start_rewind_execution.qmessagebox'),
                plain_tooltip(_ui_text('rewind.execution.start_rewind_execution.qmessagebox.body')),
            )
            return
        stop_requested = threading.Event()
        execution_guard = bind_execution_guard(
            self.operation_guard, should_stop=stop_requested.is_set,
            generation=self.operation_generation,
        )
        self._rewind_stop_requested = stop_requested
        initial = self._rewind_execution_state()
        previous_caption = self._start_rewind_button.text()
        needs_confirmation = object()

        target_factory = self._rewind_target_factory

        def run() -> object:
            # 沿用旧版顺序：先让宿主完成最小化；点击按钮时 Calc 在前台是正常状态。
            stop_requested.wait(self._rewind_foreground_settle_seconds)
            execution_guard("interface_input")
            if target_factory is None:
                raise RuntimeError("target_path_required")
            target = target_factory()
            execution_guard("interface_input")
            target.bind_foreground()

            def input_guard(capability):
                execution_guard(capability)
                target.check()
                execution_guard(capability)

            def should_stop():
                if stop_requested.is_set():
                    return True
                input_guard("interface_input")
                return stop_requested.is_set()

            # 完成前台交接才建立绑定；之后每次派发仍复核，失焦不自动续点。
            input_guard("interface_input")
            if saved_guard is not None:
                try:
                    self._service.validate_dual_target_guard(saved_guard,
                        checkpoint=lambda: execution_guard("interface_input"),
                        accepted_changes=accepted_changes)
                except RewindSourceChanged:
                    # 尚未创建输入后端；通道交付对象状态，避免异常转成文字后丢失类型。
                    return needs_confirmation
            backend = PyAutoGuiMouseBackend(operation_guard=input_guard, verify_delivery=True)
            primary_error = None
            try:
                return execute_rewind_request(request, backend=backend, capture=target.capture, should_stop=should_stop)
            except BaseException as error:
                primary_error = error
                raise
            finally:
                close_rewind_input_backend(backend, primary_error)

        def terminal(value=None, error=None):
            # PageCommitLane 仅在物理 finished 后交付；先归还热键，再允许任何嵌套提示。
            if self._rewind_stop_requested is not stop_requested:
                return
            self._rewind_stop_requested = None
            if hotkey_manager is not None:
                try:
                    hotkey_manager.stop(owner=self._rewind_hotkey_owner)
                except Exception as cleanup_error:
                    error = error or localized_text(getattr(self, 'text_catalog', None), 'auto.features.toolbox.rewind.execution_ui.terminal.f50ed8e33', '停止键收尾失败：{cleanup_error}', cleanup_error=f'{cleanup_error}')
            pending = getattr(self, "_rewind_pending_done", None)
            if pending is not None:
                self._rewind_pending_done = None
                if (getattr(self, "_rewind_restore_on_close", True)
                        and self.operation_generation is not None
                        and self.operation_generation() == self._selection_generation):
                    self._restore_rewind_window(show_dialog=False)
                self.done(pending)
                return
            if not self._rewind_context_current():
                return
            if error is not None:
                key = "rewind.execution.error." + str(error)
                message = self.text_catalog.render(key) if key in self.text_catalog.messages else ui_enum_text(self.text_catalog, str(error))
                self._on_rewind_error(message)
            elif stop_requested.is_set():
                self._on_rewind_error(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.terminal.self'))
            elif value is needs_confirmation:
                self._restore_rewind_window()
                self._start_rewind_button.setEnabled(True)
                self._start_rewind_button.setText(previous_caption)

                def resume():
                    if not self._rewind_context_current():
                        return
                    if initial != self._rewind_execution_state():
                        self._on_rewind_error(self.text_catalog.render('rewind.execution.plan_changed'))
                        return
                    self._start_rewind_execution()

                # 首次检查后依据才更新：物理收尾并归还热键后，回到界面请求确认；从未重发输入。
                QTimer.singleShot(0, self, resume)
            else:
                self._on_rewind_complete(value)

        try:
            if hotkey_manager is not None:
                hotkey_manager.start(owner=self._rewind_hotkey_owner, on_stop=stop_requested.set)
            self._start_rewind_button.setEnabled(False)
            self._start_rewind_button.setText(_ui_text('rewind.execution.start_rewind_execution.start_rewind_button'))
            self._prepare_rewind_game_foreground()
            if getattr(self, "_rewind_lane", None) is None:
                self._rewind_lane = PageCommitLane(self)
            self._rewind_lane.submit(run, terminal, lambda error: terminal(error=error),
                                     lambda error: show_input_unavailable(self, "倒带结果显示失败", error)
                                     if self._rewind_context_current() and self._rewind_stop_requested is None else None)
        except Exception as error:
            stop_requested.set()
            terminal(error=self._preference_error_text(error))

    def _rewind_context_current(self) -> bool:
        try:
            self._check_selection_context()
        except CancelledError:
            return False
        return True

    def is_rewind_running(self) -> bool:
        lane = getattr(self, "_rewind_lane", None)
        return lane is not None and lane.is_running()

    def _defer_rewind_done(self, code) -> bool:
        stop = getattr(self, "_rewind_stop_requested", None)
        if stop is not None:
            stop.set()
        if self.is_rewind_running():
            self._rewind_pending_done = code
            return True
        return False

    def shutdown(self) -> None:
        """应用退出/账号转场不重新激活旧窗口，输入资源仍等通道物理收尾。"""
        self._rewind_restore_on_close = False
        self.reject()

    def _rewind_hotkey_manager(self) -> Any | None:
        parent_getter = getattr(self, "parentWidget", None)
        parent = parent_getter() if callable(parent_getter) else None
        host = parent.window() if parent is not None else None
        return getattr(host, "global_hotkey_manager", None)

    def _on_rewind_complete(self, report: Any) -> None:
        _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
        self._restore_rewind_window()
        self._start_rewind_button.setEnabled(True)
        draws = getattr(report, "planned_draws", 0)
        quality_remaining = tuple(getattr(report, "quality_remaining_currency", ()))
        if quality_remaining:
            quality_labels = {"gold": "金", "purple": "紫", "blue": "蓝"}
            remaining_text = "、".join(
                localized_text(getattr(self, 'text_catalog', None), 'auto.features.toolbox.rewind.execution_ui.on_rewind_complete.f5f793103', '{value}剩{remaining}', value=f'{quality_labels.get(quality, quality)}', remaining=f'{remaining}')
                for quality, remaining in quality_remaining
            )
        else:
            remaining_text = localized_text(getattr(self, 'text_catalog', None), 'auto.features.toolbox.rewind.execution_ui.on_rewind_complete.f43f955cf', '剩余 {value}', value=f"{getattr(report, 'remaining_currency', 0)}")
        self._start_rewind_button.setText(_ui_text('rewind.execution.on_rewind_complete.start_rewind_button.state', draws=f'{draws}', remaining_text=f'{remaining_text}'))
        self._start_rewind_button.setToolTip(plain_tooltip(_ui_text('rewind.execution.on_rewind_complete.start_rewind_button.hint')))

    def _on_rewind_error(self, message: str) -> None:
        _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
        self._restore_rewind_window()
        self._start_rewind_button.setEnabled(True)
        self._start_rewind_button.setText(_ui_text('rewind.execution.on_rewind_error.start_rewind_button'))
        self._start_rewind_button.setToolTip(message)
        show_input_unavailable(self, "游戏内倒带", message)

    def _prepare_rewind_game_foreground(self) -> None:
        parent_getter = getattr(self, "parentWidget", None)
        parent = parent_getter() if callable(parent_getter) else None
        host = parent.window() if parent is not None else None
        if host is None or host is self:
            self._rewind_minimized_host = None
            return
        self._rewind_minimized_host = host
        self.hide()
        host.showMinimized()

    def _restore_rewind_window(self, *, show_dialog: bool = True) -> None:
        host = getattr(self, "_rewind_minimized_host", None)
        if host is not None:
            host.showNormal()
            host.raise_()
            host.activateWindow()
        if show_dialog:
            self.show()
            self.raise_()
            self.activateWindow()
        self._rewind_minimized_host = None
