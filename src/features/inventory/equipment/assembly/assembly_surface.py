# 装配窗口在选择、进度和结果间复用；后台输入结束前不恢复窗口或抢占游戏焦点。
from __future__ import annotations

import winsound
from queue import Empty, SimpleQueue

from PySide6.QtCore import QSize, Signal, Qt, QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget
from shiboken6 import isValid

from src.app.theme import current_style_sheet, themed_style
from src.ui.common.text_catalog import TextActionButton, resolve_text_catalog
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.equipment.assembly.assembly_progress import AssemblyProgress
from src.services.equipment.assembly.assembly_progress import apply_progress_event
from src.features.inventory.equipment.assembly.assembly_status_view import AssemblyStatusView


class AssemblySurface(QDialog):
    stop_requested = Signal()

    def __init__(self, parent=None, *, catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(catalog)
        self.text = self.text_catalog.render
        self.setStyleSheet(current_style_sheet())
        self.outer_layout = QVBoxLayout(self)
        self.outer_layout.setContentsMargins(0, 0, 0, 0)
        self.body_page = QWidget(self)
        self.outer_layout.addWidget(self.body_page)
        self.body_layout = QVBoxLayout(self.body_page)
        self.running = False
        self.dismissed = False
        self._status_page = None
        self.progress_model = AssemblyProgress(())
        self.automatic = False

    def _status(self):
        if self._status_page is None:
            self._status_page = QWidget(self)
            layout = QVBoxLayout(self._status_page)
            layout.setContentsMargins(20, 18, 20, 16)
            self.status_view = AssemblyStatusView(self._status_page, self.text)
            self.status_view.details_expanded.connect(self._fit_result_details)
            layout.addWidget(self.status_view, 1)
            self.status_label = self.status_view.summary
            self.progress_bar = self.status_view.progress_bar
            actions = QHBoxLayout()
            actions.addStretch()
            self.stop_button = TextActionButton(self.text("assembly.stop"), self._status_page)
            self.stop_button.clicked.connect(self.request_stop)
            self.close_button = TextActionButton(self.text("common.close"), self._status_page)
            self.close_button.clicked.connect(self.reject)
            actions.addWidget(self.stop_button)
            actions.addWidget(self.close_button)
            layout.addLayout(actions)
            self.outer_layout.addWidget(self._status_page)
        self.body_page.hide()
        self._status_page.show()
        self.stop_button.setVisible(self.automatic and self.running)
        self.close_button.setVisible(self.automatic and not self.running)

    def begin_running(self, title, message, *, visible=True):
        self._status()
        self.running = True
        self.dismissed = False
        self.setWindowTitle(title)
        self.status_view.heading.setText(title)
        self.status_label.setText(message)
        self.status_label.setStyleSheet("")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        self.stop_button.setEnabled(True)
        self.stop_button.setVisible(self.automatic)
        self.close_button.hide()
        self.setMinimumSize(0, 0)
        fit_dialog_to_available_screen(self, QSize(760, min(640, 260 + 64 * len(self.progress_model.rows))))
        if visible:
            self.setWindowModality(Qt.WindowModal)
            self.show()
        else:
            self.hide()

    def update_progress(self, message, current, total, *, show_bar=True):
        if not self.running:
            return
        self.status_label.setText(message)
        if self.progress_model.rows:
            self.status_view.render(self.progress_model, automatic=self.automatic)
        else:
            self.progress_bar.setRange(0, max(1, total))
            self.progress_bar.setValue(current)
        self.progress_bar.setVisible(show_bar)

    def request_stop(self):
        if self.running:
            self.stop_button.setEnabled(False)
            self.status_label.setText(self.text("assembly.stopping"))
            self.stop_requested.emit()

    def end_running(self):
        self.running = False
        if self._status_page is not None:
            self.stop_button.hide()
            self.progress_bar.hide()
            self.close_button.setVisible(self.automatic)

    def show_result(self, title, message, *, warning=False):
        self._status()
        self.end_running()
        self.setWindowTitle(title)
        self.status_view.heading.setText(title)
        summary = message.split("\n", 1)[0]
        if len(summary) > 160:
            summary = summary[:160] + "…"
        self.status_label.setText(summary)
        self.status_view.set_details(message, summary=summary)
        self.status_view.notice.setText(self.text("assembly.status.result_notice"))
        self.status_view.render(self.progress_model, automatic=self.automatic)
        self.status_label.setStyleSheet(themed_style("color:#f0b263;") if warning else "")
        self.close_button.setDefault(self.automatic)
        if warning and not self.dismissed:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        if not self.dismissed:
            # 隐藏态完成布局与限位，避免先映射小窗再拉伸到结果尺寸。
            self.layout().activate()
            fit_dialog_to_available_screen(self, self.size().expandedTo(QSize(760, 360)))
            self.show()

    def _fit_result_details(self, expanded):
        if self.running or not self.isVisible():
            return
        if expanded:
            self._details_collapsed_size = self.size()
            width = max(1, self.status_view.content.width())
            height = max(self.status_view.details.heightForWidth(width), self.status_view.details.sizeHint().height())
            desired = QSize(self.width(), self.height() + height + self.status_view.rows_layout.spacing())
        else:
            desired = getattr(self, "_details_collapsed_size", None)
            self._details_collapsed_size = None
            if desired is None:
                return
        # 只调整同一结果窗；少量详情按内容增高，长正文仍受屏幕限位并可滚动。
        fit_dialog_to_available_screen(self, desired)

    def configure_targets(self, targets, *, automatic=False):
        self.dismissed = False
        self.progress_model = AssemblyProgress(tuple(targets))
        self.automatic = automatic
        self._status()
        self.status_view.set_targets(targets)
        self.status_view.render(self.progress_model, automatic=automatic)
        self.status_view.details_toggle.setChecked(False)
        self.status_view.details_toggle.hide()
        self.status_view.notice.setText(self.text("assembly.status.stop_notice"))

    def render_progress(self):
        self._status()
        self.status_view.render(self.progress_model, automatic=self.automatic)

    def reject(self):
        if self.running:
            self.dismissed = True
            self.request_stop()
            self.hide()
        else:
            super().reject()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and isinstance(self.focusWidget(), QLineEdit):
            event.accept()
            return
        super().keyPressEvent(event)


def assembly_surface(window):
    current = getattr(window, "_assembly_surface", None)
    if current is None or not isValid(current):
        current = AssemblySurface(window, catalog=getattr(window, "text_catalog", None))
        window._assembly_surface = current
    return current


def release_assembly_surface(window):
    current = getattr(window, "_assembly_surface", None)
    if current is not None and isValid(current) and not current.running:
        current.close()
        current.deleteLater()
        window._assembly_surface = None


def assembly_is_running(window):
    return any(
        worker is not None and worker.isRunning()
        for worker in (
            getattr(window, "_equipment_apply_worker", None),
            getattr(window, "_automatic_equipment_apply_worker", None),
        )
    )


def deliver_after_finished(worker, result, error, *, no_result=None):
    """结果信号只暂存；线程物理退场后才展示结果或异常，避免遮挡仍在收尾的游戏输入。"""
    outcome = {}
    worker.result_ready.connect(lambda value: outcome.update(value=value))
    worker.error.connect(lambda message: outcome.update(error=message))

    def finished():
        if "error" in outcome:
            error(outcome["error"])
        elif "value" in outcome:
            result(outcome["value"])
        else:
            error(no_result or resolve_text_catalog(None).render("assembly.no_result"))

    worker.finished.connect(finished)


class AssemblyProgressChannel:
    """单任务队列：worker 仅复制事件；账号门禁、Qt 控件和投影只在 UI 线程消费。"""

    def __init__(self, surface, is_current, *, is_stopping=lambda: False):
        self.is_stopping = is_stopping
        self.surface = surface
        self.is_current = is_current
        self.queue = SimpleQueue()
        self.closed = False
        self.timer = QTimer(surface)
        self.timer.timeout.connect(self.drain)
        self.timer.start(80)

    def publish(self, payload):
        if not self.closed:
            self.queue.put(dict(payload))

    def drain(self):
        if self.is_current() and self.is_stopping() and self.surface.running:
            self.surface.status_label.setText(self.surface.text("assembly.stopping"))
            self.surface.stop_button.setEnabled(False)
        changed = False
        while True:
            try:
                event = self.queue.get_nowait()
            except Empty:
                break
            if not self.is_current():
                continue
            apply_progress_event(self.surface.progress_model, event)
            changed = True
        if changed and self.is_current():
            self.surface.render_progress()
            if not self.is_stopping():
                self.surface.status_label.setText(
                    self.surface.text("assembly.phase." + self.surface.progress_model.phase)
                )

    def close(self):
        self.drain()
        self.timer.stop()
        self.closed = True
