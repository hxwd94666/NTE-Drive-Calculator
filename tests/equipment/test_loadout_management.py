# 验证配装管理的真实事务、冻结锁、账号退场与页面恢复失败不重写。
from __future__ import annotations

from concurrent.futures import CancelledError
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import sqlite3
from threading import Event
from types import SimpleNamespace
from time import monotonic, sleep

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QVBoxLayout, QWidget

from src.domain.equipment.loadout_management import LoadoutAccount, LoadoutMutation
from src.features.inventory.equipment.persistence_controller import EquipmentPersistenceController, LoadoutOperation
from src.services.equipment.loadout_management import LoadoutManagementService
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError
from tests.equipment.test_role_loadout_slots import inventory_snapshot, assignment

NTE_TEST_TIER = "core"


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "account.sqlite3"
    native = inventory_snapshot()
    first = native["params"]["items"][0]
    native["params"]["items"] = []
    for serial in (11, 12):
        item = deepcopy(first)
        item["uid"]["serial"] = serial
        native["params"]["items"].append(item)
    native["params"]["item_count"] = 2
    with UserDataDao(path, account_id="owner", account_name="fixture") as dao:
        snapshot_id = dao.import_inventory_snapshot(native)
        plans = []
        for character_id, serial in ((1003, 11), (1004, 12)):
            item = {**assignment(), "uid_serial": serial}
            plans.append(dao.save_loadout_plan(
                name="方案", character_id=character_id, assignments=[item],
                source_snapshot_id=snapshot_id, status="ready", is_active=True,
                payload={"schema": "allocation-official-snapshot-v1", "source_role_name": str(character_id),
                         "assignment_scores": {f"nte-module-22-{serial}": 20.0}},
            ))
    return LoadoutAccount("owner", 1, path.resolve()), plans


def prepared(database, *, plan_id=None):
    account, _plans = database
    operation = LoadoutOperation(account)
    service = LoadoutManagementService()
    return service, operation, service.prepare(operation, plan_id=plan_id)


def test_clear_skips_locked_and_preserves_historical_source_scores(database):
    account, plans = database
    with UserDataDao(account.database_path) as dao:
        dao.set_allocation_lock(plans[1], True)
        history = [dao.get_loadout_plan(plan) for plan in plans]
    service, operation, snapshot = prepared(database)
    result = service.mutate(operation, LoadoutMutation("clear", snapshot))
    assert len(result.skipped) == 1
    with UserDataDao(account.database_path) as dao:
        current = dao.list_current_loadout_slot_plans()
        assert [row["plan"]["plan_id"] for row in current] == [plans[1]]
        for plan, before in zip(plans, history):
            after = dao.get_loadout_plan(plan)
            assert after["source_snapshot_id"] == before["source_snapshot_id"]
            assert after["payload"] == before["payload"]
            assert after["assignments"] == before["assignments"]
        assert dao.integrity_check()["ok"]


@pytest.mark.parametrize("change", ["lock", "replacement", "slot"])
def test_stale_confirmation_does_not_mutate_a_changed_current_scope(database, change):
    account, plans = database
    service, operation, snapshot = prepared(database, plan_id=plans[0])
    slot = snapshot.plan(plans[0])
    with UserDataDao(account.database_path) as dao:
        if change == "lock":
            dao.set_allocation_lock(plans[0], True)
        elif change == "replacement":
            dao.save_plan_to_slot(slot.slot_id, name="新方案", assignments=[assignment()],
                source_snapshot_id=slot.source_snapshot_id,
                payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "1003"})
        else:
            dao.rename_loadout_slot(slot.slot_id, "新名字")
        before = dao.get_loadout_slot(slot.slot_id)
    with pytest.raises(UserDataValidationError, match="已变化"):
        service.mutate(operation, LoadoutMutation("delete", snapshot, slot.slot_id))
    with UserDataDao(account.database_path) as dao:
        assert dao.get_loadout_slot(slot.slot_id) == before


def test_clear_failure_after_first_update_rolls_back_the_entire_batch(database):
    account, plans = database
    service, operation, snapshot = prepared(database)
    second = snapshot.plan(plans[1]).slot_id
    with sqlite3.connect(account.database_path) as connection:
        connection.execute(f"CREATE TRIGGER injected_failure BEFORE UPDATE OF current_plan_id ON role_loadout_slot WHEN OLD.slot_id = {second} BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        service.mutate(operation, LoadoutMutation("clear", snapshot))
    with UserDataDao(account.database_path) as dao:
        assert {row["plan"]["plan_id"] for row in dao.list_current_loadout_slot_plans()} == set(plans)
        assert dao.integrity_check()["ok"]


def test_slot_crud_preserves_identity_and_rejects_locked_or_final_archive(database):
    account, plans = database
    service, operation, snapshot = prepared(database, plan_id=plans[0])
    primary = snapshot.plan(plans[0])
    with pytest.raises(UserDataValidationError, match="至少保留"):
        service.mutate(operation, LoadoutMutation("archive", snapshot, primary.slot_id))
    with UserDataDao(account.database_path) as dao:
        assert dao.get_loadout_plan(plans[0])["is_active"]
        assert dao.get_loadout_slot(primary.slot_id)["current_plan_id"] == plans[0]
    service.mutate(operation, LoadoutMutation("rename", snapshot, primary.slot_id, "主槽新名"))
    snapshot = service.prepare(operation, slot_id=primary.slot_id)
    assert snapshot.slot(primary.slot_id).slot_name == "主槽新名"
    result = service.mutate(operation, LoadoutMutation("create", snapshot, name="备用"))
    snapshot = service.prepare(operation, slot_id=primary.slot_id)
    service.mutate(operation, LoadoutMutation("rename", snapshot, result.slot_id, "副本"))
    snapshot = service.prepare(operation, slot_id=primary.slot_id)
    assert snapshot.slot(result.slot_id).slot_name == "副本"
    locked = service.mutate(operation, LoadoutMutation("lock", snapshot, primary.slot_id))
    assert locked.locked
    snapshot = service.prepare(operation, slot_id=primary.slot_id)
    for action in ("delete", "archive"):
        with pytest.raises(UserDataValidationError, match="锁定"):
            service.mutate(operation, LoadoutMutation(action, snapshot, primary.slot_id))
    service.mutate(operation, LoadoutMutation("lock", snapshot, primary.slot_id))
    snapshot = service.prepare(operation, slot_id=primary.slot_id)
    service.mutate(operation, LoadoutMutation("archive", snapshot, primary.slot_id))
    with UserDataDao(account.database_path) as dao:
        visible = dao.list_loadout_slots(primary.character_id)
        assert [(row["slot_id"], row["slot_key"]) for row in visible] == [(result.slot_id, "primary")]
        assert dao.get_loadout_plan(plans[0])["source_snapshot_id"] == primary.source_snapshot_id


@pytest.mark.parametrize("accepted", [False, True])
def test_cancellation_at_commit_acceptance_has_an_honest_physical_outcome(database, accepted):
    _account, plans = database
    service, operation, snapshot = prepared(database, plan_id=plans[0])

    class Gate:
        account = operation.account
        checkpoint = operation.checkpoint

        @contextmanager
        def commit_context(self):
            if accepted:
                operation.checkpoint()
            operation.cancelled.set()
            if not accepted:
                operation.checkpoint()
            yield

    mutation = LoadoutMutation("delete", snapshot, snapshot.plan(plans[0]).slot_id)
    if accepted:
        assert service.mutate(Gate(), mutation).action == "delete"
    else:
        with pytest.raises(CancelledError):
            service.mutate(Gate(), mutation)
    with UserDataDao(operation.account.database_path) as dao:
        assert dao.is_current_loadout_slot_plan(plans[0]) is (not accepted)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def spin(app, condition, timeout=5):
    deadline = monotonic() + timeout
    while not condition() and monotonic() < deadline:
        app.processEvents()
        sleep(0.005)
    assert condition(), "后台任务未在有界等待内结束"


@pytest.mark.parametrize("after_commit", [False, True])
def test_account_retirement_waits_for_physical_worker_and_drops_old_callbacks(database, app, after_commit):
    account, plans = database
    entered, release = Event(), Event()
    delivered, errors = [], []

    class SlowService(LoadoutManagementService):
        def mutate(self, operation, mutation):
            result = super().mutate(operation, mutation) if after_commit else None
            entered.set()
            assert release.wait(4)
            return result if after_commit else super().mutate(operation, mutation)

    identity = [account]
    service = SlowService()
    controller = EquipmentPersistenceController(lambda: identity[0], service)
    # 从真实prepare入口获得owner追踪的token，再启动写入。
    handles = []
    controller.prepare(lambda value, handle: handles.append((value, handle)), errors.append, plan_id=plans[0])
    spin(app, lambda: bool(handles) and not controller.is_running())
    snapshot, operation = handles[0]
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(10)
    try:
        controller.commit(operation, LoadoutMutation("delete", snapshot, snapshot.plan(plans[0]).slot_id),
                          delivered.append, errors.append, errors.append)
        spin(app, entered.is_set)
        spin(app, lambda: len(ticks) >= 3)
        with pytest.raises(RuntimeError, match="尚未退场"):
            controller.stop()
        assert controller.is_running()
        transitions, expired = [], []
        assert controller.defer_transition("old", lambda: transitions.append("old"), lambda: expired.append(True), timeout_ms=20)
        spin(app, lambda: bool(expired))
        assert controller.is_running()  # 超时只撤销等待，不伪造物理终态或放行副作用。
        assert controller.defer_transition("cancelled-selection", lambda: transitions.append("cancelled"), lambda: None)
        controller.cancel_transition()
        assert controller.defer_transition("latest-selection", lambda: transitions.append("latest"), lambda: None)
        identity[0] = replace(account, account_id="new", generation=2, database_path=account.database_path.with_name("new.sqlite3"))
        release.set()
        spin(app, lambda: not controller.is_running())
        assert not delivered and not errors and not transitions
        assert bool(controller.last_committed_result) is after_commit
        assert not identity[0].database_path.exists()
        with UserDataDao(account.database_path) as dao:
            assert dao.is_current_loadout_slot_plan(plans[0]) is (not after_commit)
    finally:
        release.set()
        spin(app, lambda: not controller.is_running())
        timer.stop()
        controller.close()
        controller.deleteLater()


def test_real_ui_lock_and_slot_buttons_use_owner_and_projection_failure_never_replays(database, app, monkeypatch):
    from src.features.inventory.equipment import display_controller as display
    from src.features.inventory.equipment.slot_manager_dialog import LoadoutSlotDialog
    account, plans = database
    host = QWidget()
    service = LoadoutManagementService()
    host.equipment_persistence_controller = EquipmentPersistenceController(lambda: account, service, host)
    host._game_loadout_states = {}
    host._saved_equipment_states = {}
    host.invalidate_saved_equipment_cache = lambda: None
    host.refresh_saved_equipment_after_mutation = lambda **_kwargs: None
    projected = []
    monkeypatch.setattr(display, "update_equipment_role_status", lambda *_args, **kwargs: projected.append(kwargs))
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: None)
    display._toggle_role_allocation_lock(host, "1003", plan_id=plans[0], completed=lambda _value: (_ for _ in ()).throw(RuntimeError("render failed")))
    spin(app, lambda: bool(projected) and not host.equipment_persistence_controller.is_running())
    with UserDataDao(account.database_path) as dao:
        assert dao.get_loadout_plan(plans[0])["allocation_locked"]
        slot_id = dao.get_loadout_plan(plans[0])["slot_id"]
    # 相同生产管理入口打开真实对话框，普通按钮新增/重命名/归档，不调用游戏组件。
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("备用", True))
    display._manage_loadout_slot(host, slot_id, role_name="1003")
    spin(app, lambda: bool(host.findChildren(LoadoutSlotDialog)) and not host.equipment_persistence_controller.is_running())
    dialog = host.findChildren(LoadoutSlotDialog)[0]
    dialog.create_button.click()
    spin(app, lambda: dialog.selector.count() == 2 and not host.equipment_persistence_controller.is_running())
    created_id = dialog.selector.currentData()
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("副本", True))
    dialog.rename_button.click()
    spin(app, lambda: dialog.selector.currentText() == "副本" and not host.equipment_persistence_controller.is_running())
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.Yes)
    dialog.archive_button.click()
    spin(app, lambda: dialog.selector.count() == 1 and not host.equipment_persistence_controller.is_running())
    with UserDataDao(account.database_path) as dao:
        assert dao.get_loadout_slot(created_id)["is_archived"]
        assert dao.get_loadout_plan(plans[0])["allocation_locked"]
    dialog.reject()
    host.equipment_persistence_controller.close()
    host.deleteLater()


@pytest.mark.parametrize("advanced", [False, True])
def test_root_switch_failure_restores_real_context_owner_and_discards_old_projection(database, app, monkeypatch, advanced):
    from src.app.context import AccountContext, AppContext, CallbackAccountLifecycle
    from src.features.inventory.equipment.display_controller import reset_equipment_account_state
    from src.features.settings.native_plugin_update_controller import NativePluginUpdateController
    from src.ui.app import MainWindow

    account, plans = database
    directory = account.database_path.parent
    old = AccountContext("owner", "fixture", directory, account.database_path, directory, directory, directory)
    new = replace(old, active_account_id="new", user_database_path=directory / "new.sqlite3")
    with UserDataDao(new.user_database_path, account_id="new", account_name="fixture"):
        pass
    context = AppContext(SimpleNamespace(), old, settings_factory=lambda *_args, **_kwargs: None)
    host = QWidget()
    host.app_context = context
    host._account_manager = SimpleNamespace(read_index=lambda: {"accounts": [{"id": "new"}]},
        activate=lambda _key: new, set_active_account_id=lambda _key: None)
    host._equipment_assembly_is_running = lambda: False
    host.toolbox_page = SimpleNamespace(is_rewind_running=lambda: False)
    idle = SimpleNamespace(is_running=lambda: False)
    host.scanning_controller = host.identification_controller = host.battle_report_controller = idle
    # 正式维护 owner 保持 idle，让失败分支继续验证真实账号生命周期。
    host.native_plugin_update_controller = NativePluginUpdateController(
        context=context, policy=None, session=None, loader=None, generation=lambda: context.generation,
        maintenance=SimpleNamespace(uncertain=False), parent=host)
    host._inventory_sync_service = None
    host._maybe_auto_start_inventory_sync = lambda: None
    host._saved_equipment_states = {"old": {"_sqlite_plan_id": plans[0]}}
    host._equip_role_states = dict(host._saved_equipment_states)
    host.equip_role_strip, host.equip_content = QWidget(host), QWidget(host)
    host.reset_equipment_account_state = lambda: reset_equipment_account_state(host)
    controller = EquipmentPersistenceController(
        lambda: LoadoutAccount(context.account.active_account_id, context.generation, context.account.user_database_path),
        LoadoutManagementService(), host)
    host.equipment_persistence_controller = controller
    context.register_account_lifecycle(CallbackAccountLifecycle(
        is_running=controller.is_running, stop=controller.stop,
        rebuild=lambda _account: controller.rebuild(), start=lambda: None))

    def fail():
        raise RuntimeError("later lifecycle failed")

    context.register_account_lifecycle(CallbackAccountLifecycle(
        is_running=lambda: False, stop=lambda: None if advanced else fail(),
        rebuild=lambda current: fail() if advanced and current == new else None, start=lambda: None))
    context.subscribe_account_changed(lambda _event: host.reset_equipment_account_state())
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: None)
    assert MainWindow._switch_account(host, "new") is False
    assert context.account == old
    assert context.generation == (2 if advanced else 0)
    if advanced:
        assert host._saved_equipment_states == host._equip_role_states == {}
        assert not host.equip_role_strip.isEnabled() and not host.equip_content.isEnabled()
    else:
        assert host._saved_equipment_states["old"]["_sqlite_plan_id"] == plans[0]
    delivered, failed = [], []
    assert controller.prepare(lambda snapshot, _operation: delivered.append(snapshot), failed.append)
    spin(app, lambda: bool(delivered or failed) and not controller.is_running())
    assert not failed and delivered[0].account.account_id == context.account.active_account_id
    with UserDataDao(account.database_path) as dao:
        assert dao.is_current_loadout_slot_plan(plans[0])
        assert not dao.get_loadout_plan(plans[0])["allocation_locked"]
    host.native_plugin_update_controller.begin_close()
    controller.close()
    host.deleteLater()


def test_account_reset_rebuilds_an_equal_equipment_projection(app):
    from src.features.inventory.equipment.display_controller import reset_equipment_account_state
    from src.features.inventory.equipment.display_view import _publish_equipment_states

    host = QWidget()
    host.equip_role_strip, host.equip_content = QWidget(host), QWidget(host)
    host.equip_role_layout = QVBoxLayout(host.equip_role_strip)
    host.equip_content_layout = QVBoxLayout(host.equip_content)
    _publish_equipment_states(host, {})
    original = host.equip_content_layout.itemAt(0).widget()
    assert original.text()
    reset_equipment_account_state(host)
    assert host.equip_content_layout.count() == 0
    # 账号切换后相等的领域投影仍须恢复真实 Qt 内容，不能误命中旧渲染缓存。
    _publish_equipment_states(host, {})
    restored = host.equip_content_layout.itemAt(0).widget()
    assert restored is not original and restored.text() == original.text()
    assert host.equip_role_strip.isEnabled() and host.equip_content.isEnabled()
    host.deleteLater()
