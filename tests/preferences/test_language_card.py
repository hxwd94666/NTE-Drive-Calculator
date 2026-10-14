# 验证真实语言设置控件、长纯文本、非阻塞提交与关闭，组件和游戏不参与。
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json
import ctypes
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMessageBox, QWidget

from src.app.theme import apply_app_theme
from src.features.settings.language.card import LanguageCard
from src.integrations.preferences.json_store import LocalPreferencesStore
from src.integrations.preferences.locale_files import LocaleFiles
from src.services.preferences.language_settings_service import LanguageSettingsService
from src.services.preferences.global_theme_settings_service import GlobalThemeSettingsService
from src.ui.main_window_theme_mixin import MainWindowThemeMixin

ROOT = Path(__file__).resolve().parents[2]
NTE_TEST_TIER = "core"


@pytest.fixture(scope="session")
def app():
    result = QApplication.instance() or QApplication([])
    result.setQuitOnLastWindowClosed(False)
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


def test_import_save_restore_is_explicit_and_does_not_hot_switch_or_restart(app, service, monkeypatch):
    sample = ROOT / "docs/examples/locales/qps-ploc.json"
    card = LanguageCard(service)
    card.show()
    active = service.startup().catalog
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(sample), ""))
    try:
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
        QTest.mouseClick(card.save_button, Qt.LeftButton)
        wait_for(app, lambda: not card.is_running())
        assert service.preferences.read()["locale"] == "qps-ploc"
        assert "下次启动生效" in card.status.text()
        assert service.startup().catalog is active
        QTest.mouseClick(card.restore_button, Qt.LeftButton)
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
    card = LanguageCard(restarted)
    card.resize(640, card.sizeHint().height())
    card.show()
    try:
        app.processEvents()
        assert "extended layout text" in card.description.text()
        assert all(label.textFormat() == Qt.PlainText for label in card.findChildren(QLabel))
        card.save_button.setFocus()
        QTest.keyClick(card.save_button, Qt.Key_Space)
        wait_for(app, lambda: not card.is_running())
        assert restarted.preferences.read()["locale"] == "qps-ploc"
        for widget in (card.combo, card.save_button, card.restore_button, card.install_button):
            assert widget.isVisible() and widget.isEnabled()
            assert card.contentsRect().contains(widget.geometry())
    finally:
        retire(app, card)
        apply_app_theme(app, "black")


def test_blocked_file_commit_keeps_event_loop_alive_and_defers_close(app, service):
    entered, release = threading.Event(), threading.Event()
    original = service.restore_default
    def blocked():
        entered.set()
        assert release.wait(5)
        return original()
    service.restore_default = blocked
    card = LanguageCard(service)
    card.show()
    ticks = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    try:
        QTest.mouseClick(card.restore_button, Qt.LeftButton)
        wait_for(app, entered.is_set)
        assert not card.save_button.isEnabled()
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
    original = service.restore_default
    def blocked():
        entered.set()
        assert release.wait(5)
        return original()
    service.restore_default = blocked
    owner = RestartOwner()
    owner.app_context = SimpleNamespace(paths=SimpleNamespace(app_dir=ROOT))
    card = LanguageCard(service, owner)
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
        QTest.mouseClick(card.restore_button, Qt.LeftButton)
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
            QTest.mouseClick(card.restore_button, Qt.LeftButton)
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
