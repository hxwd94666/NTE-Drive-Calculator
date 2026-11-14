# 验证绘制计时只旁观本例窗口，不强制刷新或把隐藏页面记为已绘制。
import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget

from src.observability.context import OperationContext
from src.observability.stage_timings import StageTimings
from src.ui.common.paint_timings import begin_gui_timing


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def trace():
    events = []
    value = StageTimings(OperationContext.create("warehouse"),
        sink=lambda *args, **fields: events.append(fields), details_enabled=lambda: True)
    return value, events


def retire(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(widget, QEvent.DeferredDelete)


def test_hidden_view_is_not_reported_as_painted(app):
    widget = QWidget()
    timings, events = trace()
    try:
        probe = begin_gui_timing(widget, timings)
        probe.submitted()
        assert any(e.get("boundary") == "view_hidden" for e in events)
        assert not any(e.get("stage") == "gui.first_paint_dispatch" for e in events)
    finally:
        retire(widget)


def test_first_paint_is_one_shot_and_stale_owner_has_no_log(app):
    widget = QWidget()
    timings, events = trace()
    try:
        widget.show()
        app.processEvents()
        probe = begin_gui_timing(widget, timings)
        probe.submitted()
        widget.update()
        app.processEvents()
        widget.update()
        app.processEvents()
        assert sum(e.get("stage") == "gui.first_paint_dispatch" for e in events) == 1
        old, old_events = trace()
        stale = begin_gui_timing(widget, old, is_current=lambda: False)
        stale.submitted()
        widget.update()
        app.processEvents()
        assert old_events == []
    finally:
        retire(widget)
