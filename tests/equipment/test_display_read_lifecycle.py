# 验证配装正式读取入口的有界任务、真实账号转场、物理关闭与模态错误失效。
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThread, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from src.app.context import AccountContext, AppContext, ApplicationPaths
from src.app.page_tasks import close_page_tasks
from src.features.inventory.equipment import display_view as view
from src.features.inventory.equipment.display_controller import EquipmentDisplayControllerMixin

NTE_TEST_TIER = "core"


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


def until(app, condition):
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    app.processEvents()
    assert condition(), "synthetic read did not reach its bounded terminal state"


def dispose(app, host, release=None):
    if release is not None:
        release.set()
    close_page_tasks(host)
    until(app, lambda: not any(worker.isRunning() for worker in host.findChildren(QThread)))
    host.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def make_host(tmp_path, monkeypatch):
    class Host(EquipmentDisplayControllerMixin, QWidget):
        pass

    host = Host()
    paths = ApplicationPaths.from_roots(
        root=tmp_path, app_dir=tmp_path, data_root=tmp_path,
        bundled_config_dir=tmp_path / "config", asset_dir=tmp_path / "assets",
        app_icon_path=tmp_path / "assets/app_icon.ico",
    )
    account = AccountContext("first", "synthetic", tmp_path / "first", tmp_path / "first.sqlite3",
                             tmp_path / "first-config", tmp_path / "first-images", tmp_path / "first-logs")
    host.app_context = AppContext(paths, account, settings_factory=lambda *_args, **_kwargs: None)
    # 使用正式公开账号事务和生产重置钩子，不手改 account/generation/token。
    host.app_context.subscribe_account_changed(lambda _event: host.reset_equipment_account_state())
    # 非本片装配按钮只绑定 idle 回调；正式页面本身和模式切换控件实际创建。
    host._preview_fast_assemble_all_roles = host._preview_automatic_assemble_all_roles = lambda: None
    page = host._page_equipment()
    page.setParent(host)
    host.equipment_presentation = SimpleNamespace()
    host.roles_db = {}
    host.applied = []

    def observe(window, states):
        assert QThread.currentThread() is QApplication.instance().thread()
        window.applied.append(dict(states))
        window._equip_rendered_mode = getattr(window, "_equipment_mode", "saved")

    # 只隔离既有卡片绘制；真实入口、工作线程、结果应用、缓存及账号重置全部运行。
    monkeypatch.setattr(view, "_publish_equipment_states", observe)
    return host


def game_result(database_path):
    return {"projection": SimpleNamespace(supported=True, roles=("synthetic",), message=""),
            "states": {Path(database_path).stem: {}}, "saved_states": {}}


def test_account_and_mode_refreshes_keep_one_reader_and_only_latest_request(app, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    seen, concurrent = [], []
    active = 0
    lock = threading.Lock()

    def read(database_path, *, static_database_path, saved_states=None, mode):
        nonlocal active
        with lock:
            active += 1
            concurrent.append(active)
            seen.append((Path(database_path), Path(static_database_path), mode))
        try:
            entered.set()
            assert release.wait(5)
            return game_result(database_path) if mode == "game" else {Path(database_path).stem: {}}
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(view, "_load_sqlite_equipment_display_states",
                        lambda path, **kwargs: read(path, mode="saved", **kwargs))
    monkeypatch.setattr(view, "_load_game_equipment_display_states",
                        lambda path, **kwargs: read(path, mode="game", **kwargs))
    host = make_host(tmp_path, monkeypatch)
    try:
        host._refresh_equip()
        until(app, entered.is_set)
        prior = host.app_context.account
        event = host.app_context.switch_account(replace(prior, active_account_id="next",
                                                       user_database_path=tmp_path / "next.sqlite3"))
        assert event.generation == 1
        host._set_equipment_mode("game")
        for _ in range(10):
            host._refresh_equip()
        # 给错误的并行实现机会真正进入阻塞 IO，而不是只数线程对象。
        for _ in range(10):
            app.processEvents()
            time.sleep(0.005)
        assert seen == [(prior.user_database_path, host.app_context.paths.static_database_path, "saved")]
        release.set()
        until(app, lambda: bool(host.applied) and not any(worker.isRunning() for worker in host.findChildren(QThread)))
        assert [mode for _path, _static, mode in seen] == ["saved", "game"]
        assert seen[-1][0] == host.app_context.account.user_database_path
        assert max(concurrent) == 1
        assert list(host._game_loadout_states) == ["next"]
        assert [list(states) for states in host.applied] == [["next"]]
    finally:
        dispose(app, host, release)


def test_existing_close_discovery_waits_for_physical_read_and_discards_delivery(app, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def read(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)
        return {"obsolete": {}}

    monkeypatch.setattr(view, "_load_sqlite_equipment_display_states", read)
    host = make_host(tmp_path, monkeypatch)
    try:
        host._refresh_equip()
        until(app, entered.is_set)
        host._refresh_equip()
        # 与正式 MainWindow.closeEvent 相同的公开发现门禁，不运行完整窗口退出副作用。
        assert close_page_tasks(host) is True
        release.set()
        until(app, lambda: not close_page_tasks(host))
        assert host.applied == []
    finally:
        dispose(app, host, release)


def test_error_modal_account_switch_does_not_clear_new_projection(app, tmp_path, monkeypatch):
    host = make_host(tmp_path, monkeypatch)

    def read(path, **_kwargs):
        if Path(path).stem == "first":
            raise RuntimeError("synthetic first-account read error")
        return {"next": {}}

    monkeypatch.setattr(view, "_load_sqlite_equipment_display_states", read)
    modal_finished = []

    def warning(*_args):
        dialog = QMessageBox(host)
        dialog.setText("synthetic read error")
        timer = QTimer(dialog)
        timer.setInterval(5)
        timer.timeout.connect(lambda: dialog.accept() if host._saved_equipment_states.get("next") is not None else None)

        def switch():
            old = host.app_context.account
            host.app_context.switch_account(replace(old, active_account_id="next",
                                                     user_database_path=tmp_path / "next.sqlite3"))
            host._refresh_equip()
            timer.start()

        QTimer.singleShot(0, dialog, switch)
        QTimer.singleShot(3000, dialog, dialog.reject)
        dialog.exec()
        timer.stop()
        modal_finished.append(True)
        dialog.deleteLater()

    monkeypatch.setattr(view.QMessageBox, "warning", warning)
    try:
        host._refresh_equip()
        until(app, lambda: bool(modal_finished) and not any(worker.isRunning() for worker in host.findChildren(QThread)))
        assert host.app_context.account.active_account_id == "next"
        assert host.app_context.generation == 1
        assert host._saved_equipment_states == {"next": {}}
        assert host._saved_equipment_cache_valid is True
        assert host.applied[-1] == {"next": {}}
    finally:
        dispose(app, host)


@pytest.mark.parametrize("mode", ["saved", "game"])
def test_same_account_read_failure_keeps_last_explained_projection(app, tmp_path, monkeypatch, mode):
    host = make_host(tmp_path, monkeypatch)
    failure = False

    def read(path, **_kwargs):
        if failure:
            raise RuntimeError("synthetic read interruption")
        return game_result(path) if mode == "game" else {"persisted": {"frozen-score": 47}}

    monkeypatch.setattr(view, "_load_sqlite_equipment_display_states", read)
    monkeypatch.setattr(view, "_load_game_equipment_display_states", read)
    monkeypatch.setattr(view.QMessageBox, "warning", lambda *_args: None)
    try:
        host._set_equipment_mode(mode) if mode == "game" else host._refresh_equip()
        until(app, lambda: bool(host.applied) and not any(worker.isRunning() for worker in host.findChildren(QThread)))
        field = "_game_loadout_states" if mode == "game" else "_saved_equipment_states"
        previous = dict(getattr(host, field))
        applied_count = len(host.applied)
        failure = True
        host._refresh_equip()
        until(app, lambda: "失败" in host.equip_mode_status.text()
              and not any(worker.isRunning() for worker in host.findChildren(QThread)))
        assert getattr(host, field) == previous
        assert len(host.applied) == applied_count
        assert not host.equip_content.isEnabled()
        assert "原" in host.equip_mode_status.text()
        assert not host.equip_mode_status.isHidden()
        # 第二次失败仍能解释旧事实；缓存失效后的重试不能只留下“正在读取”占位。
        host._refresh_equip()
        until(app, lambda: not host.equip_mode_status.isHidden()
              and not any(worker.isRunning() for worker in host.findChildren(QThread)))
        assert getattr(host, field) == previous
        assert host._equip_rendered_mode == mode
        assert host.applied[-1] == previous
        assert not host.equip_content.isEnabled()
    finally:
        dispose(app, host)
