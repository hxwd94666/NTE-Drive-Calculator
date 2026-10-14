# 验证真实语言设置控件、长纯文本、非阻塞提交与关闭，组件和游戏不参与。
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json
import ctypes
import os
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMessageBox, QPushButton, QWidget

from src.app.theme import apply_app_theme
from src.features.settings.language.widget import LanguageSettingsWidget
from src.features.settings.page import build_tool_settings_card
from src.integrations.preferences.json_store import LocalPreferencesStore
from src.integrations.preferences.locale_files import LocaleFiles
from src.services.preferences.language_settings_service import LanguageSettingsService
from src.services.preferences.global_theme_settings_service import GlobalThemeSettingsService
from src.ui.main_window_theme_mixin import MainWindowThemeMixin
from src.ui.main_window_data_mixin import MainWindowDataMixin

ROOT = Path(__file__).resolve().parents[2]
NTE_TEST_TIER = "core"


@pytest.fixture(scope="session")
def app():
    result = QApplication.instance() or QApplication([])
    result.setQuitOnLastWindowClosed(False)
    # Windows offscreen 不一定枚举系统字体；使用本机字体验证实际中文字形尺寸，
    # 避免“全是缺字方框”的假截图及错误布局度量，不改生产字体或引入测试字体副本。
    if os.name == "nt" and "Microsoft YaHei UI" not in QFontDatabase.families():
        fonts = Path(os.environ["WINDIR"]) / "Fonts"
        for name in ("msyh.ttc", "segoeui.ttf"):
            QFontDatabase.addApplicationFontFromData((fonts / name).read_bytes())
    return result


def wait_for(app, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("synthetic language operation did not settle")


@pytest.fixture
def service(tmp_path):
    return LanguageSettingsService(LocalPreferencesStore(tmp_path / "config/global_ui_preferences.json"),
                                   LocaleFiles(ROOT / "assets/locales", tmp_path / "config/locales"))


def retire(app, card):
    wait_for(app, lambda: not card.is_running())
    card.close()
    card.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def test_import_confirm_and_choose_chinese_is_explicit_without_hot_switch_or_restart(app, service, monkeypatch):
    sample = ROOT / "docs/examples/locales/qps-ploc.json"
    card = LanguageSettingsWidget(service)
    card.show()
    active = service.startup().catalog
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(sample), ""))
    try:
        assert [button.text() for button in card.findChildren(QPushButton)] == ["确认语言", "导入文本补丁"]
        texts = [label.text() for label in card.findChildren(QLabel)]
        assert service.startup().catalog.render("settings.language.scope") not in texts
        assert service.startup().catalog.render("settings.language.restart") not in texts
        QTest.mouseClick(card.install_button, Qt.LeftButton)
        wait_for(app, lambda: not card.is_running())
        assert service.preferred_locale() == "zh-CN"
        assert card.combo.currentData() == "qps-ploc"
        assert "当前语言不变" in card.status.text()
        bad = service.preferences.path.parent / "bad-catalog.json"
        malformed_locale = json.loads(sample.read_text(encoding="utf-8"))
        malformed_locale["locale"] = "qps-Ploc"
        for data in ("broken", json.dumps(malformed_locale)):
            bad.write_text(data, encoding="utf-8")
            monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(bad), ""))
            QTest.mouseClick(card.install_button, Qt.LeftButton)
            wait_for(app, lambda: not card.is_running())
            assert "补丁缺失或格式有误" in card.status.text()
            assert card.combo.currentData() == "qps-ploc"
            assert service.preferred_locale() == "zh-CN"
        QTest.mouseClick(card.confirm_button, Qt.LeftButton)
        wait_for(app, lambda: not card.is_running())
        assert service.preferences.read()["locale"] == "qps-ploc"
        assert "下次启动生效" in card.status.text()
        assert service.startup().catalog is active
        card.combo.setCurrentIndex(card.combo.findData("zh-CN"))
        QTest.mouseClick(card.confirm_button, Qt.LeftButton)
        wait_for(app, lambda: not card.is_running())
        assert service.preferences.read()["locale"] == "zh-CN"
        assert (service.files.patch_dir / "qps-ploc.json").is_file()
        assert service.startup().catalog is active
    finally:
        retire(app, card)


@pytest.mark.parametrize("theme", ["dark", "black", "light"])
def test_pseudo_language_plain_text_keyboard_and_visible_controls(app, service, theme):
    service.install(ROOT / "docs/examples/locales/qps-ploc.json")
    service.select("qps-ploc")
    restarted = LanguageSettingsService(service.preferences, service.files)
    apply_app_theme(app, theme)
    window = SimpleNamespace(
        _card=lambda title: MainWindowDataMixin._card(None, title),
        _log_enabled=False, _toggle_log=lambda _value: None,
        _refresh_log_session_status=lambda: None, _theme_preference=theme,
        _set_theme_preference=lambda _theme: False,
    )
    tools = build_tool_settings_card(window, restarted)
    card = tools.findChild(LanguageSettingsWidget)
    assert card is not None and card.parentWidget() is tools
    assert tools.findChild(QLabel, "cardTitle").text() == "工具设置"
    tools.resize(640, tools.sizeHint().height())
    tools.show()
    try:
        app.processEvents()
        assert "extended layout text" in card.title_label.text()
        assert all(label.textFormat() == Qt.PlainText for label in card.findChildren(QLabel))
        assert card.title_label.height() >= card.title_label.heightForWidth(card.title_label.width())
        card.confirm_button.setFocus()
        QTest.keyClick(card.confirm_button, Qt.Key_Space)
        wait_for(app, lambda: not card.is_running())
        # worker 终态与 Qt 的布局请求分开排队，先应用“已保存”提示占用的高度。
        app.processEvents()
        assert restarted.preferences.read()["locale"] == "qps-ploc"
        for widget in (card.title_label, card.combo, card.confirm_button, card.install_button):
            assert widget.isVisible() and widget.isEnabled()
            assert card.contentsRect().contains(widget.geometry())
            assert widget.height() >= widget.minimumSizeHint().height()
            assert abs(widget.geometry().center().y() - card.combo.geometry().center().y()) <= 1
    finally:
        retire(app, card)
        tools.close()
        tools.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        apply_app_theme(app, "black")


def test_blocked_file_commit_keeps_event_loop_alive_and_defers_close(app, service):
    entered, release = threading.Event(), threading.Event()
    original = service.select
    def blocked(locale):
        entered.set()
        assert release.wait(5)
        return original(locale)
    service.select = blocked
    card = LanguageSettingsWidget(service)
    card.show()
    ticks = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    try:
        QTest.mouseClick(card.confirm_button, Qt.LeftButton)
        wait_for(app, entered.is_set)
        assert not card.confirm_button.isEnabled()
        wait_for(app, lambda: len(ticks) >= 3)
        card.close()
        assert card.isVisible() and card.is_running()
        release.set()
        wait_for(app, lambda: not card.is_running())
        assert not card.isVisible()
        assert service.preferences.read()["locale"] == "zh-CN"
    finally:
        release.set()
        timer.stop()
        retire(app, card)


class RestartOwner(MainWindowThemeMixin, QWidget):
    """复用真实重启入口，但所有进程启动和退出副作用在测试中替换。"""


def test_restart_waits_for_language_commit_without_starting_process(app, service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = service.select
    def blocked(locale):
        entered.set()
        assert release.wait(5)
        return original(locale)
    service.select = blocked
    owner = RestartOwner()
    owner.app_context = SimpleNamespace(paths=SimpleNamespace(app_dir=ROOT))
    card = LanguageSettingsWidget(service, owner)
    owner._theme_preference = "black"
    owner._global_theme_settings = GlobalThemeSettingsService(service.preferences.path, store=service.preferences)
    calls = []
    def confirm():
        calls.append("confirm")
        return True
    owner._prompt_restart_for_theme_change = confirm
    def launch(*_args):
        calls.append("launch")
        return 64
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(shell32=SimpleNamespace(ShellExecuteW=launch)), raising=False)
    monkeypatch.setattr(QApplication, "quit", lambda: calls.append("quit"))
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: calls.append("wait"))
    try:
        QTest.mouseClick(card.confirm_button, Qt.LeftButton)
        wait_for(app, entered.is_set)
        assert owner._set_theme_preference("dark") is False
        assert not service.preferences.path.exists()
        assert owner._theme_preference == "black"
        assert owner._restart_application_as_admin() is False
        assert calls == ["wait", "wait"]
        release.set()
        wait_for(app, lambda: not card.is_running())
        assert service.preferences.read()["locale"] == "zh-CN"
        # 确认窗口中的新提交同样须阻止保存及启动；已存在的偏好原件保持字节相同。
        saved = service.preferences.path.read_bytes()
        entered.clear(); release.clear()
        def confirm_with_commit():
            calls.append("confirm")
            QTest.mouseClick(card.confirm_button, Qt.LeftButton)
            return True
        owner._prompt_restart_for_theme_change = confirm_with_commit
        assert owner._set_theme_preference("dark") is False
        assert owner._theme_preference == "black"
        assert service.preferences.path.read_bytes() == saved
        assert "launch" not in calls and "quit" not in calls
        release.set()
        wait_for(app, lambda: not card.is_running())
        owner._prompt_restart_for_theme_change = confirm
        assert owner._set_theme_preference("dark") is True
        assert service.preferences.read() == {"locale": "zh-CN", "theme": "dark"}
        assert calls == ["wait", "wait", "confirm", "wait", "confirm", "launch", "quit"]
    finally:
        release.set()
        retire(app, card)
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
