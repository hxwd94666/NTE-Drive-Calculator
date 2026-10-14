# 将倒带方案保存与实际执行绑定到工具页对话框。
"""UI mixin for saving and executing a rewind plan."""

from __future__ import annotations

from concurrent.futures import CancelledError
from src.features.input_operation_entry import request_input_entry, show_input_unavailable

import threading
from typing import Any

from PySide6.QtWidgets import QDialog, QMessageBox

from src.app.page_tasks import PageCommitLane
from src.features.drive_assembly.input_backends import PyAutoGuiMouseBackend
from src.integrations.operation_guard import bind_execution_guard
from src.features.drive_assembly.rewind.execution import (
    RewindExecutionRequest,
    execute_rewind_request,
)
from src.features.toolbox.rewind.execution_dialog import RewindExecutionDialog


class RewindExecutionUiMixin:
    _rewind_foreground_settle_seconds = 1.0
    _rewind_hotkey_owner = "rewind_execution"

    def _save_plan(self) -> None:
        checker = getattr(self, "_check_selection_context", None)
        if checker is not None:
            try:
                checker()
            except CancelledError:
                return
        if getattr(self, "_recommendation_invalidated", False):
            QMessageBox.warning(self, "推荐已失效", "推荐输入已变化，请重新生成后再保存。")
            return
        analysis = getattr(self, "_generated_analysis", None)
        if analysis is not None:
            try:
                self._service.validate_selection(analysis.selected_slots, analysis.static_identity)
            except Exception as error:
                QMessageBox.warning(self, "推荐已失效", str(error))
                self._save_plan_button.setEnabled(False)
                return
        if not self._slots_complete():
            return
        shape_ids = [slot.shape.shape_id for slot in self._editable_slots if slot is not None]
        saver = getattr(self._service, "save_preferences", None)
        if callable(saver):
            preferences = dict(getattr(self._service, "load_preferences", lambda: {})())
            preferences["saved_rewind_shape_ids"] = shape_ids
            preferences["saved_rewind_slots"] = self._serialize_rewind_slots()
            try:
                if analysis is not None:
                    saver(preferences, expected_slots=analysis.selected_slots, static_identity=analysis.static_identity)
                else:
                    saver(preferences)
            except Exception as error:
                QMessageBox.warning(self, "方案未保存", f"原保存方案保持不变，请处理后重试。\n{error}")
                return
        self._saved_rewind_shape_ids = tuple(shape_ids)
        self._saved_rewind_slots = tuple(self._serialize_rewind_slots())
        self._save_plan_button.setText("方案已保存")

    def _configure_rewind(self) -> None:
        if not self._rewind_context_current():
            return
        if not request_input_entry(self, "interface_input", "游戏内倒带"):
            return
        if not self._rewind_context_current():
            return
        dialog = RewindExecutionDialog(self, initial=self._rewind_options)
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
            self._start_rewind_button.setText("请先保存八槽方案")
            return
        self._start_rewind_execution()

    def _start_rewind_execution(self) -> None:
        if not self._rewind_context_current() or self.is_rewind_running():
            return
        if not request_input_entry(self, "interface_input", "游戏内倒带"):
            return
        if not self._rewind_context_current() or self.is_rewind_running():
            return
        request = RewindExecutionRequest(
            qualities=self._rewind_options.qualities,
            drive_customization=self._rewind_options.drive_customization,
            shape_ids=self._saved_rewind_shape_ids,
        )
        hotkey_manager = self._rewind_hotkey_manager()
        active_owner = getattr(hotkey_manager, "active_owner", None)
        if active_owner not in (None, self._rewind_hotkey_owner):
            QMessageBox.information(
                self,
                "进行倒带",
                "当前全局停止键正由其他任务使用，请先停止该任务后再进行倒带。",
            )
            return
        stop_requested = threading.Event()
        execution_guard = bind_execution_guard(
            self.operation_guard, should_stop=stop_requested.is_set,
            generation=self.operation_generation,
        )
        self._rewind_stop_requested = stop_requested

        def run() -> object:
            stop_requested.wait(self._rewind_foreground_settle_seconds)
            # 启动延迟内取消或代次变化，不创建输入资源。
            execution_guard("interface_input")
            backend = PyAutoGuiMouseBackend(operation_guard=execution_guard)
            try:
                return execute_rewind_request(request, backend=backend, should_stop=stop_requested.is_set)
            finally:
                try:
                    backend.force_mouse_release()
                finally:
                    backend.close()

        def terminal(value=None, error=None):
            # PageCommitLane 仅在物理 finished 后交付；先归还热键，再允许任何嵌套提示。
            if self._rewind_stop_requested is not stop_requested:
                return
            self._rewind_stop_requested = None
            if hotkey_manager is not None:
                try:
                    hotkey_manager.stop(owner=self._rewind_hotkey_owner)
                except Exception as cleanup_error:
                    error = error or f"停止键收尾失败：{cleanup_error}"
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
                self._on_rewind_error(error)
            elif stop_requested.is_set():
                self._on_rewind_error("倒带输入已停止；已派发操作的游戏结果请自行核对。")
            else:
                self._on_rewind_complete(value)

        try:
            if hotkey_manager is not None:
                hotkey_manager.start(owner=self._rewind_hotkey_owner, on_stop=stop_requested.set)
            self._start_rewind_button.setEnabled(False)
            self._start_rewind_button.setText("倒带中…")
            self._prepare_rewind_game_foreground()
            if getattr(self, "_rewind_lane", None) is None:
                self._rewind_lane = PageCommitLane(self)
            self._rewind_lane.submit(run, terminal, lambda error: terminal(error=error),
                                     lambda error: show_input_unavailable(self, "倒带结果显示失败", error)
                                     if self._rewind_context_current() and self._rewind_stop_requested is None else None)
        except Exception as error:
            stop_requested.set()
            terminal(error=str(error))

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
        self._restore_rewind_window()
        self._start_rewind_button.setEnabled(True)
        draws = getattr(report, "planned_draws", 0)
        quality_remaining = tuple(getattr(report, "quality_remaining_currency", ()))
        if quality_remaining:
            quality_labels = {"gold": "金", "purple": "紫", "blue": "蓝"}
            remaining_text = "、".join(
                f"{quality_labels.get(quality, quality)}剩{remaining}"
                for quality, remaining in quality_remaining
            )
        else:
            remaining_text = f"剩余 {getattr(report, 'remaining_currency', 0)}"
        self._start_rewind_button.setText(f"输入已结束：计划 {draws} 次，预计{remaining_text}")
        self._start_rewind_button.setToolTip("次数与余额来自执行前计算，尚未取得游戏后验确认；请核对游戏结果，不自动重试。")

    def _on_rewind_error(self, message: str) -> None:
        self._restore_rewind_window()
        self._start_rewind_button.setEnabled(True)
        self._start_rewind_button.setText("重新进行倒带")
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
