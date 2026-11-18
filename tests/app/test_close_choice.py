# 验证关闭选择与真正退出分流，不提前停止后台任务或递归原生关闭事件。
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QRect, QTimer, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMainWindow, QPushButton

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from src.ui.main_window.main_window_lifecycle import MainWindowLifecycleMixin
from src.ui.main_window.tray_controller import TrayController


NTE_TEST_TIER = "core"


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.close_count = 0
        self.cancel_exit = False

    def closeEvent(self, event):
        self.close_count += 1
        event.ignore() if self.cancel_exit else event.accept()


@pytest.fixture
def scene():
    application()
    windows, controllers = [], []

    def create(choice="cancel", *, available=True):
        window = Window()
        calls = []
        controller = TrayController(window, available=lambda: available,
                                    choose_close=lambda owner, tray: (calls.append((owner, tray)), choice)[1])
        windows.append(window)
        controllers.append(controller)
        window.show()
        application().processEvents()
        return window, controller, calls

    yield create
    for controller in controllers:
        controller.close()
    for window in windows:
        window.cancel_exit = False
        dispose(window)


@pytest.mark.parametrize("choice", ["cancel", "unknown"])
def test_cancel_or_unknown_choice_keeps_window_and_owners(scene, choice):
    window, tray, calls = scene(choice)
    tray.request_window_close()
    assert window.isVisible() and window.close_count == 0
    assert calls == [(window, True)]


@pytest.mark.parametrize("available", [True, False])
def test_minimize_does_not_enter_exit_lifecycle(scene, available):
    window, tray, calls = scene("minimize", available=available)
    with patch.object(tray.tray, "showMessage"):
        tray.request_window_close()
    assert window.close_count == 0
    assert (not window.isVisible()) if available else (window.isVisible() and window.isMinimized())
    assert calls == [(window, available)]
    tray.restore()
    assert window.isVisible() and not window.isMinimized()


def test_exit_choice_uses_normal_close_and_cancelled_save_keeps_window(scene):
    window, tray, calls = scene("exit")
    window.cancel_exit = True
    tray.request_window_close()
    assert window.close_count == 1 and window.isVisible()
    window.cancel_exit = False
    tray.request_window_close()
    assert window.close_count == 2 and not window.isVisible()
    assert len(calls) == 2


def test_explicit_tray_exit_does_not_ask_minimize_question(scene):
    window, tray, calls = scene("minimize")
    tray.request_exit()
    assert window.close_count == 1 and not window.isVisible()
    assert calls == []


def test_reentrant_close_opens_one_question_and_closed_owner_is_inert(scene):
    window, tray, calls = scene()

    def choose(owner, available):
        calls.append((owner, available))
        tray.request_window_close()
        return "cancel"

    tray._choose_close = choose
    tray.request_window_close()
    assert calls == [(window, True)] and window.close_count == 0
    tray.close()
    tray.request_window_close()
    assert calls == [(window, True)] and window.close_count == 0


@pytest.mark.parametrize("spontaneous,saving_session", [(False, False), (True, True)])
def test_programmatic_and_session_close_skip_question(scene, spontaneous, saving_session):
    window, tray, calls = scene("minimize")

    class Event(QCloseEvent):
        def spontaneous(self):
            return spontaneous

    event = Event()
    assert tray.handle_close_event(event, saving_session=saving_session) is False
    assert calls == [] and window.close_count == 0 and event.isAccepted()


@pytest.mark.parametrize("choice,continues_exit", [("exit", True), ("minimize", False), ("cancel", False)])
def test_native_close_is_resolved_without_nested_close(scene, choice, continues_exit):
    window, tray, calls = scene(choice)

    class Event(QCloseEvent):
        def spontaneous(self):
            return True

    event = Event()
    with patch.object(tray.tray, "showMessage"):
        assert tray.handle_close_event(event, saving_session=False) is not continues_exit
    assert window.close_count == 0  # 原事件继续退场；选择时不递归发起第二次 close。
    assert event.isAccepted() is continues_exit and len(calls) == 1


def test_mainwindow_native_exit_continues_existing_draft_gate(scene, monkeypatch):
    window, tray, calls = scene("exit")
    window.tray_controller = tray
    gate = []
    monkeypatch.setattr("src.ui.controllers.configuration_controller.defer_page_transition",
                        lambda owner, resume: (gate.append(owner), True)[1])

    class Event(QCloseEvent):
        def spontaneous(self):
            return True

    event = Event()
    MainWindowLifecycleMixin.closeEvent(window, event)
    assert gate == [window] and calls == [(window, True)]
    assert not event.isAccepted() and window.close_count == 0


@pytest.mark.parametrize("choice", ["exit", "minimize", "cancel"])
def test_choice_dialog_has_explicit_actions_and_titlebar_close_cancels(scene, monkeypatch, choice):
    from src.ui.main_window.tray_controller import choose_close_action
    window, _, _ = scene()
    original_exec = QDialog.exec

    def inspect_and_choose(box):
        buttons = box.action_buttons
        assert {'exit', 'minimize'} == buttons.keys()
        assert bool(box.windowFlags() & Qt.WindowCloseButtonHint)
        if choice == "cancel":
            QTimer.singleShot(0, box, box.close)
        else:
            button = buttons[choice]
            QTimer.singleShot(0, box, button.click)
        return original_exec(box)

    monkeypatch.setattr(QDialog, "exec", inspect_and_choose)
    assert choose_close_action(window, True) == choice
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not any(dialog.isVisible() for dialog in window.findChildren(QDialog))
    assert window.isVisible() and window.close_count == 0


def test_escape_cancels_choice_without_closing_owner(scene, monkeypatch):
    from src.ui.main_window.tray_controller import choose_close_action
    window, _, _ = scene()
    original_exec = QDialog.exec

    def escape(box):
        QTimer.singleShot(0, box, lambda: QTest.keyClick(box, Qt.Key_Escape))
        return original_exec(box)

    monkeypatch.setattr(QDialog, "exec", escape)
    assert choose_close_action(window, False) == "cancel"
    assert window.isVisible() and window.close_count == 0


def test_keyboard_can_reach_both_actions_and_activate_focused_action(scene, monkeypatch):
    from src.ui.main_window.tray_controller import choose_close_action
    window, _, _ = scene()
    original_exec = QDialog.exec
    observed = []
    expected = []

    def navigate(box):
        def inspect():
            for _ in range(6):
                QTest.keyClick(box, Qt.Key_Tab)
                focused = box.focusWidget()
                if isinstance(focused, QPushButton):
                    observed.append(next(key for key, button in box.action_buttons.items() if button is focused))
                    if len(set(observed)) == 2:
                        break
            focused = box.focusWidget()
            if isinstance(focused, QPushButton):
                expected.append(next(key for key, button in box.action_buttons.items() if button is focused))
                QTest.keyClick(focused, Qt.Key_Space)
            else:
                box.close()

        QTimer.singleShot(0, box, inspect)
        return original_exec(box)

    monkeypatch.setattr(QDialog, "exec", navigate)
    result = choose_close_action(window, True)
    assert set(observed) == {'exit', 'minimize'}
    assert expected == [result]


@pytest.mark.parametrize("theme", ["black"])
def test_small_screen_actions_are_reachable_and_enter_does_not_exit(scene, monkeypatch, theme):
    from src.app.theme import apply_app_theme, current_theme_name
    from src.app.window_geometry import fit_dialog_to_available_screen
    from src.ui.main_window import tray_controller, close_choice

    app = application()
    previous_theme = current_theme_name(app)
    window, _, _ = scene(available=False)
    original_exec = QDialog.exec
    observed = []

    def fit(box, *_args, **_kwargs):
        return fit_dialog_to_available_screen(box, *_args, available_geometry=QRect(0, 0, 640, 600), **_kwargs)

    def inspect_and_enter(box):
        def inspect():
            # 真正显示后核对可操作性，不固化像素、配色或按钮内部层级。
            observed.append(all(button.isVisible() and button.isEnabled()
                                and box.rect().contains(button.mapTo(box, button.rect().topLeft()))
                                and box.rect().contains(button.mapTo(box, button.rect().bottomRight()))
                                for button in box.findChildren(QPushButton)))
            observed.append("最小化" in {button.text() for button in box.findChildren(QPushButton)})
            observed.append(box.width() <= 640 and box.height() <= 600)
            QTest.keyClick(box, Qt.Key_Return)
            observed.append(box.isVisible())
            box.close()

        QTimer.singleShot(0, box, inspect)
        return original_exec(box)

    monkeypatch.setattr(close_choice, "fit_dialog_to_available_screen", fit)
    monkeypatch.setattr(QDialog, "exec", inspect_and_enter)
    try:
        apply_app_theme(app, theme)
        assert tray_controller.choose_close_action(window, False) == "cancel"
        assert observed == [True, True, True, True]
        assert window.isVisible() and window.close_count == 0
    finally:
        apply_app_theme(app, previous_theme)
