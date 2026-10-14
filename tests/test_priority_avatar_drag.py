# 验证优先级头像区域短点击、长按拖拽和拖拽取消不误移除角色。
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from src.features.allocation import priority_role_button as drag_module
from src.features.allocation.role_selector import RoleSelector


@pytest.fixture
def selector():
    app = QApplication.instance() or QApplication([])
    widget = RoleSelector()
    widget.load_roles({"A": {}, "B": {}, "C": {}}, [])
    widget.selected, widget.priority_links = ["A", "B", "C"], [">", "="]
    widget._render_priority_row()
    widget.show()
    app.processEvents()
    # 首轮重排留下待删除旧按钮；夹具只把当前控件交给手势测试，不保留悬空 C++ 句柄。
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    yield widget
    widget.close()
    widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def button_for(selector, role):
    return next(button for button in selector.findChildren(drag_module.PriorityRoleButton)
                if button.accessibleName() == role)


def avatar_position(button):
    avatar = next(label for label in button.findChildren(QLabel) if label.accessibleName().endswith("头像"))
    return avatar.mapTo(button, avatar.rect().center())


def capture_drag(monkeypatch):
    calls = []

    class Drag:
        def __init__(self, source):
            self.source = source

        def setMimeData(self, mime):
            self.mime = mime

        def setPixmap(self, pixmap):
            pass

        def setHotSpot(self, point):
            pass

        def exec(self, action):
            calls.append((self.source.role, self.mime.text(), action))
            return Qt.IgnoreAction

    monkeypatch.setattr(drag_module, "QDrag", Drag)
    return calls


def test_avatar_short_click_removes_only_on_release(selector):
    button = button_for(selector, "A")
    pos = avatar_position(button)
    QTest.mousePress(button, Qt.LeftButton, pos=pos)
    assert selector.get_selected() == ["A", "B", "C"]
    QTest.mouseRelease(button, Qt.LeftButton, pos=pos)
    assert selector.get_selected() == ["B", "C"]


def test_avatar_hold_then_cancel_keeps_role_and_next_click_still_works(selector, monkeypatch):
    calls = capture_drag(monkeypatch)
    button = button_for(selector, "A")
    pos = avatar_position(button)
    QTest.mousePress(button, Qt.LeftButton, pos=pos)
    QTest.qWait(QApplication.startDragTime() + 40)
    QTest.mouseRelease(button, Qt.LeftButton, pos=pos)
    assert calls == [("A", "0", Qt.MoveAction)]
    assert selector.get_selected() == ["A", "B", "C"]
    assert selector.get_priority_groups() == [["A"], ["B", "C"]]
    QTest.mouseClick(button, Qt.LeftButton, pos=pos)
    assert selector.get_selected() == ["B", "C"]


def test_avatar_movement_starts_drag_without_deselecting(selector, monkeypatch):
    calls = capture_drag(monkeypatch)
    button = button_for(selector, "A")
    pos = avatar_position(button)
    QTest.mousePress(button, Qt.LeftButton, pos=pos)
    end = pos + QPoint(QApplication.startDragDistance() + 1, 0)
    event = QMouseEvent(QEvent.MouseMove, QPointF(end), QPointF(button.mapToGlobal(end)),
                        Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(button, event)
    QTest.mouseRelease(button, Qt.LeftButton, pos=end)
    assert calls == [("A", "0", Qt.MoveAction)]
    assert selector.get_selected() == ["A", "B", "C"]
