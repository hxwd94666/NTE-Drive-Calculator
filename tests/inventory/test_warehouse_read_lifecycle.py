# 验证真实仓库页面的读取合并、账号隔离、关闭收尾和冻结来源。
from __future__ import annotations

import threading
import time
import weakref
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget

from src.features.inventory.warehouse import controller as page_module
from src.features.inventory.warehouse.read_controller import (
    WarehouseReadController, read_warehouse_request, reset_warehouse_account,
)
from src.domain.inventory.warehouse_read_request import WarehouseReadRequest

NTE_TEST_TIER = "core"


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


def until(app, condition):
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.003)
    assert condition(), "仓库任务未在验收时限内结束"


def close_host(app, host):
    host.warehouse_read_controller.close()
    until(app, lambda: not host.warehouse_read_controller.is_running())
    host.close()
    host.deleteLater()
    # processEvents 的本地循环不保证处理延迟删除，验收必须真正释放页面。
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def snapshot(identity):
    return {"snapshot_id": identity, "source": "nte_core", "items": [
        {"uid": f"fixture-{identity}", "kind": "module", "state_known": True,
         "search_text": f"fixture-{identity}", "locked": False, "discarded": False,
         "title": "合成驱动", "item_name": "合成形状", "suit_name": "合成套装"},
    ]}


def make_host(monkeypatch, tmp_path, reader):
    monkeypatch.setattr(page_module, "WarehouseReadController",
                        lambda parent: WarehouseReadController(parent, reader=reader))

    class Host(page_module.WarehouseControllerMixin, QWidget):
        pass

    host = Host()
    host.app_context = SimpleNamespace(
        account=SimpleNamespace(active_account_id="fixture-a", user_database_path=tmp_path / "a.sqlite3"),
        generation=1,
        paths=SimpleNamespace(static_database_path=tmp_path / "static-a.sqlite3", game_ui_asset_root=tmp_path / "assets-a"),
    )
    page = host._page_warehouse()
    page.setParent(host)
    return host


def test_latest_refresh_and_account_switch_share_one_physical_reader(app, monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()
    seen = []
    concurrency = []
    active = 0
    mutex = threading.Lock()

    def reader(request):
        nonlocal active
        with mutex:
            active += 1
            concurrency.append(active)
            seen.append(request)
        try:
            if request.generation == 1:
                entered.set()
                assert release.wait(5)
            return snapshot(request.generation)
        finally:
            with mutex:
                active -= 1

    host = make_host(monkeypatch, tmp_path, reader)
    try:
        host._refresh_warehouse()
        until(app, entered.is_set)
        reset_warehouse_account(host)
        host.app_context.account = SimpleNamespace(active_account_id="fixture-b", user_database_path=tmp_path / "b.sqlite3")
        for generation in range(2, 27):
            host.app_context.generation = generation
            host._refresh_warehouse()
        assert host.warehouse_model.rowCount() == 0
        assert len(seen) == 1  # 慢读取期间，重复请求没有增加后台 IO。
        release.set()
        until(app, lambda: not host.warehouse_read_controller.is_running())
        assert [request.generation for request in seen] == [1, 26]
        assert [request.account_id for request in seen] == ["fixture-a", "fixture-b"]
        assert max(concurrency) == 1
        assert host._warehouse_snapshot_id == 26
        assert [item["uid"] for item in host._warehouse_all_items] == ["fixture-26"]
    finally:
        release.set()
        close_host(app, host)


def test_refresh_failure_keeps_same_account_projection_but_not_old_account(app, monkeypatch, tmp_path):
    failing = False

    def reader(request):
        if failing:
            raise RuntimeError("fixture read failure")
        return snapshot(request.generation)

    host = make_host(monkeypatch, tmp_path, reader)
    try:
        host._refresh_warehouse()
        until(app, lambda: not host.warehouse_read_controller.is_running())
        failing = True
        host._refresh_warehouse()
        until(app, lambda: not host.warehouse_read_controller.is_running())
        assert host._warehouse_snapshot_id == 1
        assert host.warehouse_model.rowCount() == 1
        assert "fixture read failure" in host.warehouse_hint.text()
        reset_warehouse_account(host)
        host.app_context.account = SimpleNamespace(active_account_id="fixture-b", user_database_path=tmp_path / "b.sqlite3")
        host.app_context.generation = 2
        host._refresh_warehouse()
        until(app, lambda: not host.warehouse_read_controller.is_running())
        assert host._warehouse_snapshot_id is None
        assert host.warehouse_model.rowCount() == 0
        assert host._warehouse_pending_state_changes == {}
    finally:
        close_host(app, host)


def test_close_drops_late_delivery_and_queued_reads(app, tmp_path):
    entered, release = threading.Event(), threading.Event()
    seen, applied, errors = [], [], []

    def reader(request):
        seen.append(request.generation)
        entered.set()
        assert release.wait(5)
        return snapshot(request.generation)

    owner = WarehouseReadController(reader=reader)
    try:
        for generation in (1, 2):
            request = WarehouseReadRequest("fixture", generation, tmp_path / "a", tmp_path / "s", tmp_path / "icons")
            owner.refresh(request, apply=applied.append, failed=errors.append)
            if generation == 1:
                until(app, entered.is_set)
        owner.close()
        release.set()
        until(app, lambda target=owner: not target.is_running())
        assert seen == [1]
        assert applied == errors == []
    finally:
        release.set()
        owner.close()
        until(app, lambda target=owner: not target.is_running())
    # 无 QWidget 父对象的读取 owner 也必须退役；保留闭包会在延迟析构时崩溃。
    reference = weakref.ref(owner)
    del owner
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert reference() is None


def test_read_request_uses_frozen_database_and_assets(monkeypatch, tmp_path):
    from src.features.inventory.warehouse import read_controller, presenter
    from src.services.inventory import warehouse_inventory_service as service_module
    from src.services.inventory import warehouse_visual_catalog as visual_module

    request = WarehouseReadRequest("fixture-a", 7, tmp_path / "a.sqlite3", tmp_path / "static.sqlite3", tmp_path / "assets")
    seen = []
    monkeypatch.setattr(read_controller, "load_warehouse_snapshot",
                        lambda path, operation, **kwargs: seen.append((path, operation, kwargs)) or {})
    read_warehouse_request(request)
    path, operation, kwargs = seen.pop()
    assert path == request.database_path
    assert operation.context_generation == 7
    assert kwargs == {"static_database_path": request.static_database_path, "asset_root": request.asset_root}
    rows = [{"kind": "core"}]

    class Service:
        def __init__(self, path, **kwargs):
            seen.append((path, kwargs))

        def load_current_snapshot(self):
            return {"snapshot_id": 4, "source": "vision", "rows": rows}

    monkeypatch.setattr(presenter, "WarehouseInventoryService", Service)
    monkeypatch.setattr(presenter, "warehouse_item_view", lambda row, **kwargs: seen.append(kwargs) or row)
    result = presenter.load_warehouse_snapshot(request.database_path, static_database_path=request.static_database_path,
                                              asset_root=request.asset_root)
    assert result["items"] == rows
    assert seen[0][1]["static_database_path"] == request.static_database_path
    assert seen[1]["static_database_path"] == request.static_database_path
    assert seen[1]["asset_root"] == request.asset_root
    monkeypatch.setattr(service_module, "StaticGameDataDao", lambda path: path)
    service = service_module.WarehouseInventoryService(request.database_path, static_database_path=request.static_database_path)
    assert service.static_dao_factory() == request.static_database_path
    visual_module.representative_core_item_id.cache_clear()

    class StaticDao:
        def __init__(self, path):
            seen.append(path)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def list_equipment_items(self, kind):
            return [{"suit_id": "fixture-suit", "quality": "gold", "item_id": "fixture-item"}]

    monkeypatch.setattr(visual_module, "StaticGameDataDao", StaticDao)
    assert visual_module.representative_core_item_id("fixture-suit", "gold", request.static_database_path) == "fixture-item"
    assert seen[-1] == request.static_database_path
    visual_module.representative_core_item_id.cache_clear()


@pytest.mark.parametrize("state_operation", [False, True])
def test_late_read_preserves_edits_and_refreshes_after_owner_releases(app, monkeypatch, tmp_path, state_operation):
    entered, release = threading.Event(), threading.Event()
    seen = []

    def reader(request):
        seen.append(request.generation)
        sequence = len(seen)
        if sequence == 2:
            entered.set()
            assert release.wait(5)
        return snapshot(sequence)

    monkeypatch.setattr(page_module, "request_input_entry", lambda *_args: True)
    host = make_host(monkeypatch, tmp_path, reader)
    try:
        host._refresh_warehouse()
        until(app, lambda: not host.warehouse_read_controller.is_running())
        host._refresh_warehouse()
        until(app, entered.is_set)
        if state_operation:
            host._set_warehouse_management_busy(True)
        else:
            host._toggle_warehouse_item_state(host.warehouse_model.index(0), "locked")
            assert host._warehouse_pending_state_changes == {"fixture-1": "locked"}
        release.set()
        until(app, lambda: not host.warehouse_read_controller.is_running())
        assert host._warehouse_snapshot_id == 1
        assert host._warehouse_deferred_snapshot_id == 2
        assert [item["uid"] for item in host._warehouse_all_items] == ["fixture-1"]
        if state_operation:
            host._set_warehouse_management_busy(False)
        else:
            assert host._warehouse_pending_state_changes == {"fixture-1": "locked"}
            host._toggle_warehouse_item_state(host.warehouse_model.index(0), "normal")
        until(app, lambda: host._warehouse_snapshot_id == 3 and not host.warehouse_read_controller.is_running())
        assert host._warehouse_pending_state_changes == {}
        assert len(seen) == 3
        assert host._warehouse_deferred_snapshot_id is None
    finally:
        release.set()
        close_host(app, host)


def test_unconfirmed_write_survives_late_read_and_modal_event_loop(app, monkeypatch, tmp_path):
    from PySide6.QtCore import QEventLoop, QTimer

    entered, release = threading.Event(), threading.Event()
    seen = []
    current_snapshot = 1

    def reader(request):
        seen.append(request.generation)
        if len(seen) == 2:
            entered.set()
            assert release.wait(5)
        return snapshot(current_snapshot)

    host = make_host(monkeypatch, tmp_path, reader)

    def modal_warning(*_args):
        # 真正执行 Qt 嵌套事件循环，让迟到的读取在结果回调中交付。
        release.set()
        loop = QEventLoop()
        QTimer.singleShot(30, loop.quit)
        loop.exec()
        until(app, lambda: not host.warehouse_read_controller.is_running())

    monkeypatch.setattr(page_module.QMessageBox, "warning", modal_warning)
    try:
        host._refresh_warehouse()
        until(app, lambda: not host.warehouse_read_controller.is_running())
        host._refresh_warehouse()
        until(app, entered.is_set)
        host._on_warehouse_state_applied(SimpleNamespace(
            before_snapshot_id=1, after_snapshot_id=None, verified=False,
            inventory_reduction_observed=False, verification_error="fixture pending confirmation",
            changes=({"uid": "fixture-1", "target_state": "locked"},),
            summary={"discard_set_count": 0, "lock_set_count": 1, "discard_clear_count": 0, "lock_clear_count": 0},
        ))
        app.processEvents()
        assert len(seen) == 2
        assert host._warehouse_all_items[0]["locked"] is True
        assert host._warehouse_confirmation_after == 1
        # 用户显式读取仍可执行，但旧快照不覆盖待确认状态或伪装成确认。
        host._refresh_warehouse()
        until(app, lambda: not host.warehouse_read_controller.is_running())
        assert host._warehouse_all_items[0]["locked"] is True
        assert "等待" in host.warehouse_hint.text()
        current_snapshot = 2
        host._on_warehouse_sync_state(SimpleNamespace(phase="listening", last_snapshot_id=2))
        until(app, lambda: host._warehouse_snapshot_id == 2 and not host.warehouse_read_controller.is_running())
        assert host._warehouse_confirmation_after is None
        assert [item["uid"] for item in host._warehouse_all_items] == ["fixture-2"]
    finally:
        release.set()
        close_host(app, host)
