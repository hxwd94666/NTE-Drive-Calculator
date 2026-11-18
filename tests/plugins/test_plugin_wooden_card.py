# 验证自定义打桩卡片的独立开关、后验状态和三主题键盘可操作性。
import pytest
from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from src.app.theme import apply_app_theme
from src.features.plugins.page import PluginsPage
from tests.plugins.test_plugins import make_service


class Wheel(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.enabled, self.status, self.owned_count = False, '未开启', 0
        self.calls = []

    def set_enabled(self, enabled):
        self.calls.append(enabled)
        self.enabled = enabled
        self.status = '正在启用' if enabled else '关闭待确认'
        self.changed.emit()


@pytest.mark.parametrize('theme', ["black"])
def test_wooden_card_keeps_independent_controls_and_confirmed_states(tmp_path, theme):
    app = application()
    original = (app.styleSheet(), app.palette(), app.property('nte_theme_preference'), app.property('nte_effective_theme'))
    apply_app_theme(app, theme)
    service, session, core, policy = make_service(tmp_path)
    wheel, applied = Wheel(), []
    page = PluginsPage(service=service, request_apply=lambda: applied.append(True),
                       show_detection=lambda: None, quick_wheel=wheel)
    before = service.settings
    try:
        page.resize(640, 620)
        page.show()
        app.processEvents()
        labels = [label.text() for label in page.findChildren(QLabel)]
        assert '自定义打桩' in labels and '游戏内快捷轮盘' not in labels
        assert not any('开启后替换原' in text or '关闭后恢复' in text for text in labels)
        assert page.wheel_toggle.accessibleName() == '自定义打桩'
        assert page.wheel_badge.text() == '已关闭'
        assert page.wheel_status.text() == '自有木桩：0'
        hint = next(label for label in page.findChildren(QLabel) if label.text().startswith('按住 Tab'))
        assert page.wheel_status.mapTo(page, QPoint()).y() < hint.mapTo(page, QPoint()).y()
        assert page.rect().contains(page.wheel_toggle.mapTo(page, page.wheel_toggle.rect().bottomRight()))
        page.wheel_toggle.setFocus()
        app.processEvents()
        assert page.wheel_toggle.hasFocus()
        QTest.keyClick(page.wheel_toggle, Qt.Key_Space)
        assert wheel.calls == [True] and page.wheel_toggle.isChecked()
        assert page.wheel_badge.text() == '正在启用'
        wheel.status, wheel.owned_count = '已启用', 3
        wheel.changed.emit()
        assert page.wheel_badge.text() == '已启用' and page.wheel_status.text() == '自有木桩：3'
        policy.enabled = False
        page.refresh()
        assert page.wheel_toggle.isEnabled()  # 降级后仍可关闭已有选择。
        QTest.keyClick(page.wheel_toggle, Qt.Key_Space)
        assert wheel.calls == [True, False]
        assert page.wheel_badge.text() == '关闭待确认'
        assert page.wheel_status.text() == '自有木桩：3'
        assert not page.wheel_toggle.isEnabled()
        wheel.status = '已关闭'
        wheel.changed.emit()
        assert page.wheel_badge.text() == '已关闭'
        assert service.settings == before and not applied and not core.calls
    finally:
        dispose(page)
        session.close()
        wheel.deleteLater()
        app.setStyleSheet(original[0])
        app.setPalette(original[1])
        app.setProperty('nte_theme_preference', original[2])
        app.setProperty('nte_effective_theme', original[3])


def test_wheel_save_failure_keeps_original_selection(tmp_path, monkeypatch):
    application()
    service, session, core, _ = make_service(tmp_path)
    wheel = Wheel()
    errors = []
    def fail(_enabled):
        raise OSError('preference_write_failed')
    wheel.set_enabled = fail
    monkeypatch.setattr('src.features.plugins.page.QMessageBox.warning', lambda *args: errors.append(args[1:]))
    page = PluginsPage(service=service, request_apply=lambda: None, show_detection=lambda: None, quick_wheel=wheel)
    try:
        page.wheel_toggle.click()
        assert not wheel.enabled and not page.wheel_toggle.isChecked()
        assert errors == [('自定义打桩', 'preference_write_failed')]
        assert not core.calls
    finally:
        dispose(page)
        session.close()
        wheel.deleteLater()
