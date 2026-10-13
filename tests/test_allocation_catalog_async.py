# 验证计算目录后台校验、有界请求、失败门禁和过期回调隔离。
from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from src.ui.controllers.allocation_catalog_controller import allocation_catalog_controller


def wait(predicate):
    until = time.monotonic() + 5
    while not predicate() and time.monotonic() < until:
        QTest.qWait(5)
    assert predicate()


@pytest.fixture
def page(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.app_context = SimpleNamespace(generation=1, account=SimpleNamespace(active_account_id="fixture", user_database_path=tmp_path / "user.sqlite3"),
        paths=SimpleNamespace(config_dir=tmp_path, equipment_allocation_database_path=tmp_path / "static.sqlite3", equipment_allocation_asset_root=tmp_path))
    window.btn_run = QPushButton(window)
    window.selector = QWidget(window)
    running = [False]
    window.scanning_controller = SimpleNamespace(is_running=lambda: running[0], role_selector=window.selector)
    window._read_allocation_catalog = lambda *_args: None
    applied = []
    def apply(loaded, *, reload_priority, source_key):
        applied.append((loaded, reload_priority, source_key))
        window._allocation_catalog_loaded_key = source_key
    window._apply_allocation_catalog = apply
    window._apply_inventory_status = lambda _summary: None
    controller = allocation_catalog_controller(window)
    monkeypatch.setattr(controller.service, "inventory_summary", lambda: None)
    yield window, controller, applied, running
    controller.close()
    wait(lambda: not controller.lane.is_running())
    window.close()
    window.deleteLater()
    app.processEvents()


def test_read_is_background_and_calculation_waits_for_current_catalog(page, monkeypatch):
    window, owner, applied, _running = page
    release, entered, threads, started = threading.Event(), threading.Event(), [], []
    def read(**_args):
        threads.append(threading.get_ident())
        entered.set()
        release.wait(2)
        return "loaded", "semantic", True
    monkeypatch.setattr(owner.service, "read", read)
    owner.refresh(continuation=lambda: started.append("started"), verify=True)
    wait(entered.is_set)
    assert not window.btn_run.isEnabled() and not window.selector.isEnabled()
    assert not applied and not started
    release.set()
    wait(lambda: bool(started))
    assert threads == [threads[0]] and threads[0] != threading.get_ident()
    assert applied == [("loaded", False, "semantic")] and window.btn_run.isEnabled()


def test_cancelled_result_cached_by_service_still_applies_to_current_request(page, monkeypatch):
    window, owner, applied, _running = page
    release, entered, calls = threading.Event(), threading.Event(), []
    def read(**_args):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            release.wait(2)
        return "latest", "changed", len(calls) == 1
    monkeypatch.setattr(owner.service, "read", read)
    window._allocation_catalog_loaded_key = "old"
    owner.refresh()
    wait(entered.is_set)
    owner.invalidate()
    owner.refresh()
    release.set()
    wait(lambda: not owner.lane.is_running())
    assert len(calls) == 2 and applied == [("latest", False, "changed")]


def test_same_revision_validation_does_not_reapply_cards(page, monkeypatch):
    window, owner, applied, _running = page
    window._allocation_catalog_loaded_key = "semantic"
    monkeypatch.setattr(owner.service, "read", lambda **_kwargs: ("same", "semantic", False))
    owner.refresh()
    wait(lambda: not owner.lane.is_running())
    assert applied == [] and window.selector.isEnabled()


def test_failure_keeps_old_catalog_and_does_not_start_calculation(page, monkeypatch):
    window, owner, applied, _running = page
    window._allocation_catalog_loaded_key = "old"
    started = []
    def read(**_kwargs):
        raise RuntimeError("fixture failed")
    monkeypatch.setattr(owner.service, "read", read)
    owner.refresh(continuation=lambda: started.append(1), verify=True)
    wait(lambda: not owner.lane.is_running())
    assert applied == [] and not started and not window.btn_run.isEnabled()
    assert window._allocation_catalog_loaded_key == "old"


def test_account_change_and_close_discard_old_results(page, monkeypatch):
    window, owner, applied, _running = page
    release, entered = threading.Event(), threading.Event()
    def read(**_kwargs):
        entered.set()
        release.wait(2)
        return "obsolete", "obsolete", True
    monkeypatch.setattr(owner.service, "read", read)
    owner.refresh()
    wait(entered.is_set)
    window.app_context.generation += 1
    release.set()
    wait(lambda: not owner.lane.is_running())
    assert not applied


def test_running_calculation_keeps_its_frozen_catalog(page, monkeypatch):
    _window, owner, applied, running = page
    running[0] = True
    monkeypatch.setattr(owner.service, "read", lambda **_kwargs: pytest.fail("running request must not reload"))
    owner.refresh()
    assert not applied and not owner.lane.is_running()


def test_leaving_page_while_validation_pending_does_not_start_hidden_task(page, monkeypatch):
    window, owner, _applied, _running = page
    window.stack = SimpleNamespace(currentIndex=lambda: 1)
    window._nav_key_for_index = lambda _index: "home"
    started = []
    monkeypatch.setattr(owner.service, "read", lambda **_kwargs: ("same", "same", True))
    owner.refresh(continuation=lambda: started.append(1), verify=True)
    wait(lambda: not owner.lane.is_running())
    assert not started


@pytest.fixture
def priority_account_window(tmp_path, monkeypatch):
    from src.app.context import AccountContext, AppContext, ApplicationPaths
    from src.features.allocation.role_selector import RoleSelector
    from src.ui.controllers.configuration_controller import register_page_configuration_lifecycle
    from src.ui.main_window_data_mixin import MainWindowDataMixin

    class Window(MainWindowDataMixin, QWidget):
        pass

    def account(name):
        root = tmp_path / name
        return AccountContext(name, name, root, root / "user.sqlite3", root / "config",
                              root / "images", root / "logs")

    app = QApplication.instance() or QApplication([])
    window = Window()
    first, second = account("a"), account("b")
    window.app_context = AppContext(ApplicationPaths.from_roots(
        root=tmp_path, app_dir=tmp_path, data_root=tmp_path, bundled_config_dir=tmp_path / 'bundle-config',
        asset_dir=tmp_path / 'assets', app_icon_path=tmp_path / 'assets/app.ico',
    ), first)
    selector = RoleSelector(priority_config_path_provider=lambda: (
        window.app_context.account.user_config_dir / "priority_config.json"
    ))
    window.scanning_controller = SimpleNamespace(role_selector=selector, is_running=lambda: False)
    window.btn_run = QPushButton(window)
    window.equipment_presentation = SimpleNamespace(update_catalog=lambda **_kwargs: None)
    window.identification_controller = SimpleNamespace(update_catalog=lambda **_kwargs: None)
    window._apply_inventory_status = lambda _summary: None
    loaded = ({}, [], [], {}, {"A": {}, "B": {}}, {}, {}, None, {})
    selector.load_roles(loaded[4], [])
    selector.load_startup_priority_config()
    selector.selected = ["A", "B"]
    selector.priority_links = ["="]
    for name, value in {
        "custom_sets": {"A": "Set"}, "custom_weapons": {"A": "Fork"},
        "crit_rate_caps": {"A": 74}, "crit_rate_cap_sources": {"A": "manual"},
        "tape_main_filters": {"A": []}, "tape_main_filter_override_roles": {"A"},
        "stat_priority_configs": {"A": {"stats": []}}, "stat_priority_override_roles": {"A"},
        "set_effect_modes": {"A": "none"},
    }.items():
        setattr(selector, name, value)
    fields = ("selected", "priority_links", "custom_sets", "custom_weapons", "crit_rate_caps",
              "crit_rate_cap_sources", "tape_main_filters", "tape_main_filter_override_roles",
              "stat_priority_configs", "stat_priority_override_roles", "set_effect_modes")
    releases = []

    def catalog(*, delayed=False):
        owner = window._catalog_controller()
        release, entered = threading.Event(), threading.Event()
        releases.append(release)
        def read(**_kwargs):
            entered.set()
            if delayed:
                release.wait(2)
            return loaded, window.app_context.account.active_account_id, True
        monkeypatch.setattr(owner.service, "read", read)
        monkeypatch.setattr(owner.service, "inventory_summary", lambda: None)
        return owner, release, entered

    catalog()
    unregister = register_page_configuration_lifecycle(window.app_context, lambda: (
        getattr(window, "allocation_catalog_controller", None),
    ))
    yield window, selector, first, second, fields, catalog
    for release in releases:
        release.set()
    owner = window.allocation_catalog_controller
    owner.close()
    wait(lambda: not owner.lane.is_running())
    unregister()
    selector.close()
    window.close()
    window.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("broken", [False, True])
def test_priority_account_reload_clears_only_old_account_and_preserves_bad_target(
    priority_account_window, monkeypatch, broken,
):
    import json
    window, selector, _first, second, fields, catalog = priority_account_window
    target = second.user_config_dir / "priority_config.temp.json"
    if broken:
        target.parent.mkdir(parents=True)
        target.write_text("{", encoding="utf-8")
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.warning", lambda *_args: None)
    window.app_context.switch_account(second)
    owner, _release, _entered = catalog()
    window._load_data()
    assert all(not getattr(selector, name) for name in fields)
    with pytest.raises(RuntimeError):
        selector.save_temporary_priority_config()
    wait(lambda: not owner.lane.is_running())
    assert all(not getattr(selector, name) for name in fields)
    if broken:
        with pytest.raises(RuntimeError):
            selector.save_temporary_priority_config()
        assert target.read_text("utf-8") == "{"
    else:
        selector.save_temporary_priority_config()
        assert json.loads(target.read_text("utf-8"))["priority_list"] == []


def test_priority_context_gap_and_cancelled_catalog_never_overwrite_unread_target(priority_account_window):
    window, selector, first, second, fields, catalog = priority_account_window
    selector.save_temporary_priority_config()
    first_path = first.user_config_dir / "priority_config.temp.json"
    original = first_path.read_bytes()
    target = second.user_config_dir / "priority_config.temp.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"priority_list": ["B"]}')
    expected = target.read_bytes()
    window.app_context.switch_account(second)
    # 上下文已推进，MainWindow尚未进入_load_data，退出调用同一public保存方法。
    blocked = False
    try:
        selector.save_temporary_priority_config()
    except RuntimeError:
        blocked = True
    assert target.read_bytes() == expected
    assert blocked
    owner, release, entered = catalog(delayed=True)
    window._load_data()
    wait(entered.is_set)
    assert all(not getattr(selector, name) for name in fields)
    owner.close()
    with pytest.raises(RuntimeError):
        selector.save_temporary_priority_config()
    release.set()
    wait(lambda: not owner.lane.is_running())
    assert first_path.read_bytes() == original and target.read_bytes() == expected


def test_priority_failed_switch_and_same_account_bad_load_keep_exit_save_valid(priority_account_window, monkeypatch):
    from copy import deepcopy
    import json
    from src.app.context import CallbackAccountLifecycle
    window, selector, first, second, fields, _catalog = priority_account_window
    before = {name: deepcopy(getattr(selector, name)) for name in fields}
    def fail_stop():
        raise RuntimeError("owner still running")
    window.app_context.register_account_lifecycle(CallbackAccountLifecycle(
        is_running=lambda: False, stop=fail_stop, rebuild=lambda _account: None, start=lambda: None,
    ))
    with pytest.raises(RuntimeError, match="owner still running"):
        window.app_context.switch_account(second)
    assert window.app_context.account == first and window.app_context.generation == 0
    first.user_config_dir.mkdir(parents=True)
    (first.user_config_dir / "priority_config.json").write_text("{", encoding="utf-8")
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.warning", lambda *_args: None)
    selector.load_priority_config()
    assert before == {name: getattr(selector, name) for name in fields}
    selector.save_temporary_priority_config()
    saved = json.loads((first.user_config_dir / "priority_config.temp.json").read_text("utf-8"))
    assert saved["priority_list"] == ["A", "B"] and not second.user_config_dir.exists()
