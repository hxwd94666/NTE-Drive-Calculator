# 验证无边框主窗口对中键移动和拖拽释放状态保持安全。
from __future__ import annotations

import unittest

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QMainWindow

from src.ui.app import MainWindow


def mouse_event(
    event_type: QEvent.Type,
    *,
    button: Qt.MouseButton,
    buttons: Qt.MouseButton,
) -> QMouseEvent:
    return QMouseEvent(
        event_type,
        QPointF(100, 100),
        QPointF(100, 100),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def bare_main_window() -> MainWindow:
    QApplication.instance() or QApplication([])
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window.resize(640, 480)
    window._resize_margin = 8
    window._drag_pos = None
    if hasattr(window, "_drag_edges"):
        del window._drag_edges
    return window


class MainWindowMouseEventTests(unittest.TestCase):
    def test_middle_button_move_is_safe_before_any_left_drag(self):
        window = bare_main_window()
        event = mouse_event(
            QEvent.Type.MouseMove,
            button=Qt.MouseButton.NoButton,
            buttons=Qt.MouseButton.MiddleButton,
        )

        window.mouseMoveEvent(event)

        self.assertIsNone(window._drag_pos)
        self.assertEqual((False,) * 4, window._drag_edges)

    def test_middle_release_does_not_cancel_an_active_left_drag(self):
        window = bare_main_window()
        window._drag_pos = QPoint(100, 100)
        window._drag_edges = (True, False, False, False)

        window.mouseReleaseEvent(
            mouse_event(
                QEvent.Type.MouseButtonRelease,
                button=Qt.MouseButton.MiddleButton,
                buttons=Qt.MouseButton.LeftButton,
            )
        )

        self.assertEqual(QPoint(100, 100), window._drag_pos)
        self.assertEqual((True, False, False, False), window._drag_edges)

        window.mouseReleaseEvent(
            mouse_event(
                QEvent.Type.MouseButtonRelease,
                button=Qt.MouseButton.LeftButton,
                buttons=Qt.MouseButton.NoButton,
            )
        )
        self.assertIsNone(window._drag_pos)
        self.assertEqual((False,) * 4, window._drag_edges)


if __name__ == "__main__":
    unittest.main()
