# 将倒带方案保存与实际执行绑定到工具页对话框。
"""UI mixin for saving and executing a rewind plan."""

from __future__ import annotations

import threading
from typing import Any

from PySide6.QtWidgets import QDialog, QMessageBox

from src.i18n import tr
from src.app.workers import WorkerThread
from src.features.drive_assembly.rewind_execution import (
    RewindExecutionRequest,
    execute_rewind_request,
)
from src.features.toolbox.rewind_execution_dialog import RewindExecutionDialog


class RewindExecutionUiMixin:
    _rewind_foreground_settle_seconds = 1.0
    _rewind_hotkey_owner = "rewind_execution"

    def _save_plan(self) -> None:
        if not self._slots_complete():
            return
        shape_ids = [slot.shape.shape_id for slot in self._editable_slots if slot is not None]
        saver = getattr(self._service, "save_preferences", None)
        if callable(saver):
            preferences = dict(getattr(self._service, "load_preferences", lambda: {})())
            preferences["saved_rewind_shape_ids"] = shape_ids
            preferences["saved_rewind_slots"] = self._serialize_rewind_slots()
            saver(preferences)
        self._saved_rewind_shape_ids = tuple(shape_ids)
        self._saved_rewind_slots = tuple(self._serialize_rewind_slots())
        self._save_plan_button.setText(tr("方案已保存"))

    def _configure_rewind(self) -> None:
        dialog = RewindExecutionDialog(self, initial=self._rewind_options)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._rewind_options = dialog.options()
        self._save_preferences()
        if self._rewind_options.drive_customization == "apply_plan" and len(self._saved_rewind_shape_ids) != 8:
            self._start_rewind_button.setText(tr("请先保存八槽方案"))
            return
        self._start_rewind_execution()

    def _start_rewind_execution(self) -> None:
        current_worker = self._rewind_worker
        if current_worker is not None:
            try:
                if current_worker.isRunning():
                    return
            except RuntimeError:
                if self._rewind_worker is current_worker:
                    self._rewind_worker = None
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
                tr("进行倒带"),
                tr("当前全局停止键正由其他任务使用，请先停止该任务后再进行倒带。"),
            )
            return
        stop_requested = threading.Event()
        if hotkey_manager is not None:
            hotkey_manager.start(
                owner=self._rewind_hotkey_owner,
                on_stop=stop_requested.set,
            )
        self._start_rewind_button.setEnabled(False)
        self._start_rewind_button.setText(tr("倒带中…"))
        self._prepare_rewind_game_foreground()

        def run() -> object:
            stop_requested.wait(self._rewind_foreground_settle_seconds)
            return execute_rewind_request(
                request,
                should_stop=stop_requested.is_set,
            )

        worker = WorkerThread(target=run, parent=self)
        self._rewind_worker = worker
        worker.result_ready.connect(self._on_rewind_complete)
        worker.error.connect(self._on_rewind_error)
        worker.finished.connect(self._stop_rewind_hotkeys)
        worker.finished.connect(lambda: self._release_rewind_worker(worker))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _release_rewind_worker(self, worker: WorkerThread) -> None:
        if self._rewind_worker is worker:
            self._rewind_worker = None

    def _rewind_hotkey_manager(self) -> Any | None:
        parent_getter = getattr(self, "parentWidget", None)
        parent = parent_getter() if callable(parent_getter) else None
        host = parent.window() if parent is not None else None
        return getattr(host, "global_hotkey_manager", None)

    def _stop_rewind_hotkeys(self) -> None:
        hotkey_manager = self._rewind_hotkey_manager()
        if hotkey_manager is not None:
            hotkey_manager.stop(owner=self._rewind_hotkey_owner)

    def _on_rewind_complete(self, report: Any) -> None:
        self._restore_rewind_window()
        self._start_rewind_button.setEnabled(True)
        draws = getattr(report, "planned_draws", 0)
        quality_remaining = tuple(getattr(report, "quality_remaining_currency", ()))
        if quality_remaining:
            quality_labels = {"gold": tr("金"), "purple": tr("紫"), "blue": tr("蓝")}
            remaining_text = "、".join(
                tr("{quality}剩{remaining}",
                   quality=quality_labels.get(quality, quality), remaining=remaining)
                for quality, remaining in quality_remaining
            )
        else:
            remaining_text = tr("剩余 {value}", value=getattr(report, "remaining_currency", 0))
        self._start_rewind_button.setText(
            tr("倒带完成 {draws} 次，{remaining}", draws=draws, remaining=remaining_text)
        )

    def _on_rewind_error(self, message: str) -> None:
        self._restore_rewind_window()
        self._start_rewind_button.setEnabled(True)
        self._start_rewind_button.setText(tr("重新进行倒带"))
        self._start_rewind_button.setToolTip(message)

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

    def _restore_rewind_window(self) -> None:
        host = getattr(self, "_rewind_minimized_host", None)
        if host is not None:
            host.showNormal()
            host.raise_()
            host.activateWindow()
        self.show()
        self.raise_()
        self.activateWindow()
        self._rewind_minimized_host = None
