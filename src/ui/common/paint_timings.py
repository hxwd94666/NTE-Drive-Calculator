# 观测一次结果提交后的首个绘制事件与事件循环延迟；不触发重绘、不采截图。
from __future__ import annotations

from time import perf_counter

from PySide6.QtCore import QEvent, QObject, QTimer, Qt


class _FirstPaint(QObject):
    def __init__(self, widget, timings, is_current):
        super().__init__(widget)
        self._widget, self._timings, self._is_current = widget, timings, is_current
        self._finished = False
        self._submitted = False
        self._last_tick = perf_counter()
        self._max_lag_ms = 0.0
        self._ticks = 0
        self._heartbeat = QTimer(self)
        self._heartbeat.setTimerType(Qt.PreciseTimer)
        self._heartbeat.setInterval(50)
        self._heartbeat.timeout.connect(self._tick)
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(3000)
        self._deadline.timeout.connect(lambda: self.finish("paint_unobserved"))
        widget.installEventFilter(self)
        self._heartbeat.start()
        self._deadline.start()

    def _tick(self):
        now = perf_counter()
        self._max_lag_ms = max(self._max_lag_ms, (now - self._last_tick) * 1000 - 50)
        self._last_tick = now
        self._ticks += 1

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Paint and self._submitted and not self._finished:
            # 这里只证明 Qt 开始派发绘制，不宣称已呈现到屏幕或整页全部绘制完成。
            self._timings.since("model_submitted", "gui.first_paint_dispatch")
            if (perf_counter() - self._last_tick) * 1000 >= 50:
                self._tick()
            self.finish("first_paint_dispatch")
        return False

    def submitted(self):
        self._timings.mark("model_submitted")
        self._submitted = True
        if not self._widget.isVisible():
            self.finish("view_hidden")

    def finish(self, boundary):
        if self._finished:
            return
        self._finished = True
        self._widget.removeEventFilter(self)
        self._heartbeat.stop()
        self._deadline.stop()
        if self._is_current():
            if self._ticks:
                self._timings.note("gui.heartbeat", heartbeat_count=self._ticks,
                                   peak_lag_ms=max(0.0, self._max_lag_ms))
            outcome = 'failed' if boundary in {'gui_failed', 'gui_rejected'} else 'cancelled' if boundary == 'paint_superseded' else 'returned'
            self._timings.finish(boundary, outcome=outcome)
        self.deleteLater()


def begin_gui_timing(widget, timings, *, is_current=lambda: True):
    if widget is None or timings is None:
        return
    for probe in widget.findChildren(_FirstPaint, options=Qt.FindDirectChildrenOnly):
        probe.finish("paint_superseded")
    return _FirstPaint(widget, timings, is_current)
