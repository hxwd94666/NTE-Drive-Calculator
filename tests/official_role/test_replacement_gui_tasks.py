# 用真实Qt入口和合成SQLite保护单次SHA、冻结选择、取消接受点及提交事实。
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import sqlite3
from threading import Event, Thread, get_ident
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton, QWidget
from shiboken6 import isValid

from src.app.context import AccountContext, AppContext
from src.app.page_tasks import PageCommitLane, PageTaskLane
from src.features.inventory.equipment.plan_optimizer import _open_official_saved_plan_optimizer, _optimize_saved_equipment
from src.features.inventory.warehouse.result_card import WarehouseResultCard
from src.features.official_role.replacement_controller import show_official_role_replacement
from src.features.official_role.replacement_tasks import ReplacementTaskOwner
from src.features.battle_report.editing.equipment_replacement import show_battle_equipment_replacement
from src.integrations._static_release_pin import WindowsStaticFilePin
from src.integrations.static_catalog_release import StaticCatalogReleaseReader
from src.optimizer.scoring import ScoringEngine
from src.services.official_role.page import load_official_role_detail
from src.services.official_role.replacement import save_official_role_replacement
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.official_role.static_build_fixture import CHARACTER_ID, build_fixture

NTE_TEST_TIER = "core"


def pump(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(5)
    assert predicate(), "替换owner未在固定预算内物理退场"


def plan_count(path):
    with closing(sqlite3.connect(path)) as db:
        return db.execute("SELECT COUNT(*) FROM loadout_plan").fetchone()[0]


def manifest(path):
    with closing(sqlite3.connect(path)) as db:
        dataset, importer, _stamp = db.execute("SELECT * FROM dataset").fetchone()
        schema = db.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0]
    with path.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    path.with_name("manifest.json").write_text(json.dumps({"catalog_scope": "game", "database": {
        "filename": path.name, "dataset_id": dataset, "schema_version": schema,
        "sha256": sha, "size_bytes": path.stat().st_size, "source_payloads_omitted": True,
    }, "build_tool": {"importer_version": importer}}), encoding="utf-8")


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    connection, temporary, config = build_fixture(tmp_path / "release")
    connection.close()
    path = temporary.with_name("game_static.sqlite3")
    temporary.rename(path)
    manifest(path)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "manifest.json").write_text("{}", encoding="utf-8")
    account_path = tmp_path / "old" / "user.sqlite3"
    def item(serial):
        return {"uid": {"serial": serial, "slot": 17}, "kind": "core", "item_id": "fixture-core",
                "suit_id": "fixture-suit", "quality": "orange", "level": 80, "max_level": 80,
                "locked": False, "equipped": False,
                "main_stats": [{"property_id": "AtkUp", "value": 0.375, "percent": True}],
                "sub_stats": [{"property_id": "CritBase", "value": 0.1, "percent": True}]}
    with UserDataDao(account_path, account_id="gui-old") as user:
        snapshot = user.import_inventory_snapshot(
            {"complete": True, "item_count": 2, "items": [item(1), item(2)]}, source="nte_core")
        current = user.import_inventory_snapshot(
            {"complete": True, "item_count": 1, "items": [item(3)]}, source="nte_core")
        slot = user.create_loadout_slot(CHARACTER_ID, "target", slot_key="primary")
        donor = user.create_loadout_slot(CHARACTER_ID + 1, "donor", slot_key="primary")
        plan = user.save_plan_to_slot(slot, name="original", assignments=[{"uid_serial": 1, "uid_slot": 17,
            "kind": "core"}], source_snapshot_id=snapshot, score=10,
            payload={"assignment_scores": {"nte-core-17-1": 10}})
        donor_plan = user.save_plan_to_slot(donor, name="donor", assignments=[{"uid_serial": 2, "uid_slot": 17,
            "kind": "core"}], source_snapshot_id=snapshot, score=10,
            payload={"assignment_scores": {"nte-core-17-2": 10}})
    account = AccountContext("gui-old", "合成账号", tmp_path / "old", account_path,
                             tmp_path / "old/config", tmp_path / "old/screens", tmp_path / "old/log")
    context = AppContext(SimpleNamespace(static_database_path=path, shared_database_path=tmp_path / "shared.sqlite3",
        role_catalog=SimpleNamespace(asset_root=assets, dataset_id="gui", sha256="0" * 64)), account)
    window = QWidget()
    window.app_context = context
    window.scoring_engine = ScoringEngine(config, roles_db={})
    window._shape_areas = {}
    refreshed = []
    window._refresh_equip = lambda: refreshed.append(get_ident())
    window.show()
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **_kw: messages.append(args[2]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **_kw: messages.append(args[2]))
    detail = load_official_role_detail(account_path, CHARACTER_ID, static_database_path=path, asset_root=assets)
    yield {"app": app, "window": window, "context": context, "account": account_path, "path": path,
           "detail": detail, "target": detail["equipment_contexts"]["saved"]["items"][0],
           "slot": slot, "donor": donor, "plan": plan, "donor_plan": donor_plan,
           "snapshot": snapshot, "current": current, "messages": messages, "refreshed": refreshed}
    pump(lambda: not any(lane.is_running() for lane in window.findChildren(PageTaskLane))
         and not any(lane.is_running() for lane in window.findChildren(PageCommitLane)))
    for owner in window.findChildren(ReplacementTaskOwner):
        owner.close()
    window.close()
    window.deleteLater()
    app.processEvents()


def automate_confirm(app, *, after_started=None):
    timer = QTimer()
    timer.setInterval(5)
    submitted = []
    deadline = time.monotonic() + 5
    def act():
        if time.monotonic() >= deadline:
            timer.stop()
            for widget in app.topLevelWidgets():
                if isinstance(widget, QDialog) and widget.isVisible():
                    widget.reject()
            return
        dialog = next((widget for widget in app.topLevelWidgets()
                       if isinstance(widget, QDialog) and widget.objectName() == "equipmentReplacementDialog"
                       and widget.isVisible()), None)
        if dialog is None:
            return
        if not submitted:
            group = dialog.findChild(QWidget, "equipmentReplacementCandidateGroup")
            cards = group.findChildren(WarehouseResultCard)
            assert cards
            QTest.mouseClick(cards[0], Qt.LeftButton)
            button = next(button for button in dialog.findChildren(QPushButton) if button.text() == "确定替换")
            button.click()
            button.click()  # 接受一次之后禁用：这不是自动重试。
            submitted.append(dialog)
        if after_started is not None:
            after_started(dialog)
    timer.timeout.connect(act)
    timer.start()
    return timer, submitted


@pytest.mark.parametrize("entry", ["inventory", "eager"])
def test_actual_click_prepares_once_then_confirms_once_on_workers(fixture, monkeypatch, entry):
    actual, gui, observed = WindowsStaticFilePin.sha256, get_ident(), []
    def observed_sha(pin):
        observed.append(get_ident())
        return actual(pin)
    monkeypatch.setattr(WindowsStaticFilePin, "sha256", observed_sha)
    timer, submitted = automate_confirm(fixture["app"])
    try:
        if entry == "inventory":
            assert _open_official_saved_plan_optimizer(fixture["window"], "合成角色", "tape", "nte-core-17-1",
                                                       plan_id=fixture["plan"])
        else:
            detail = fixture["detail"]
            assert show_official_role_replacement(fixture["window"], detail, fixture["target"],
                static_database_path=detail["static_database_path"], static_schema_version=detail["static_schema_version"],
                static_release=detail["static_release"])
    finally:
        timer.stop()
    assert len(submitted) == 1 and len(observed) == 2 and all(thread != gui for thread in observed)
    pump(lambda: not any(owner.is_running() for owner in fixture["window"].findChildren(ReplacementTaskOwner)))
    with UserDataDao(fixture["account"]) as user:
        slot = user.get_loadout_slot(fixture["slot"])
        saved = user.get_loadout_plan(slot["current_plan_id"])
        assert saved["plan_id"] != fixture["plan"] and saved["source_snapshot_id"] == fixture["snapshot"]
        assert saved["assignments"][0]["uid_serial"] == 2
        assert user.current_inventory_snapshot_id() == fixture["current"]
        assert plan_count(fixture["account"]) == 4  # 原target/donor与事务新target/补位donor各一份。
        assert "static_release" not in json.dumps(saved["payload"])
    assert not any("替换失败" in message for message in fixture["messages"])


@pytest.mark.parametrize("boundary", ["role", "hidden", "account"])
def test_actual_late_read_drops_selection_and_retires_before_account_switch(fixture, monkeypatch, boundary):
    entered, release, current = Event(), Event(), [True]
    actual = WindowsStaticFilePin.sha256
    def slow(pin):
        entered.set()
        assert release.wait(3)
        return actual(pin)
    monkeypatch.setattr(WindowsStaticFilePin, "sha256", slow)
    context = fixture["context"]
    old = context.account
    new = replace(old, active_account_id="gui-new", user_database_path=fixture["account"].with_name("new.sqlite3"))
    transition_errors = []
    timer = QTimer()
    timer.setInterval(5)
    def invalidate():
        if not entered.is_set():
            return
        timer.stop()
        if boundary == "hidden":
            fixture["window"].hide()
        elif boundary == "role":
            current[0] = False
        else:
            try:
                context.switch_account(new)
            except RuntimeError as exc:
                transition_errors.append(str(exc))
    timer.timeout.connect(invalidate)
    timer.start()
    applied = []
    try:
        assert not show_battle_equipment_replacement(fixture["window"], fixture["detail"], fixture["target"],
            context_key="saved", static_database_path=fixture["path"], static_schema_version=fixture["detail"]["static_schema_version"],
            on_replaced=applied.append, is_current=lambda: current[0])
        owners = fixture["window"].findChildren(ReplacementTaskOwner)
        assert len(owners) == 1 and owners[0].is_running() and isValid(owners[0])
        assert context.account == old and context.generation == 0 and not applied
        if boundary == "account":
            assert transition_errors
    finally:
        timer.stop()
        release.set()
    pump(lambda: not any(owner.is_running() for owner in fixture["window"].findChildren(ReplacementTaskOwner)))
    fixture["app"].processEvents()
    context.switch_account(new)
    assert context.account == new and context.generation == 1 and not applied
    # 真实write-open证明DAO/pin已物理释放，而非仅仅丢了result signal。
    with fixture["path"].open("r+b") as stream:
        assert stream.read(16) == b"SQLite format 3\0"


@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
def test_cancelled_confirmation_preserves_actual_transaction_boundary(fixture, monkeypatch, phase):
    reached, release = Event(), Event()
    if phase == "before_commit":
        actual = UserDataDao.save_loadout_plan
        def intercept(dao, *args, **kwargs):
            value = actual(dao, *args, **kwargs)
            if kwargs.get("slot_id") == fixture["slot"]:
                reached.set()
                assert release.wait(3)
            return value
        monkeypatch.setattr(UserDataDao, "save_loadout_plan", intercept)
    else:
        actual = UserDataDao.save_replacement_plan_to_slot
        def intercept(dao, *args, **kwargs):
            value = actual(dao, *args, **kwargs)
            reached.set()
            assert release.wait(3)
            return value
        monkeypatch.setattr(UserDataDao, "save_replacement_plan_to_slot", intercept)
    def cancel(dialog):
        if reached.is_set():
            dialog.reject()
    timer, submitted = automate_confirm(fixture["app"], after_started=cancel)
    detail = fixture["detail"]
    try:
        assert not show_official_role_replacement(fixture["window"], detail, fixture["target"],
            static_database_path=detail["static_database_path"], static_schema_version=detail["static_schema_version"],
            static_release=detail["static_release"])
        assert submitted and reached.is_set()
        assert any(owner.is_running() for owner in fixture["window"].findChildren(ReplacementTaskOwner))
    finally:
        timer.stop()
        release.set()
    pump(lambda: not any(owner.is_running() for owner in fixture["window"].findChildren(ReplacementTaskOwner)))
    with UserDataDao(fixture["account"]) as user:
        current = user.get_loadout_slot(fixture["slot"])["current_plan_id"]
        assert (current == fixture["plan"]) == (phase == "before_commit")
        assert plan_count(fixture["account"]) == (2 if phase == "before_commit" else 4)
    assert not fixture["refreshed"]  # 取消只抑制UI acknowledgement，不自动重发。


def test_stale_target_and_concurrent_locked_owner_reject_without_overwrite(fixture, monkeypatch):
    with UserDataDao(fixture["account"]) as user:
        new_plan = user.save_plan_to_slot(fixture["slot"], name="new", assignments=[{"uid_serial": 1,
            "uid_slot": 17, "kind": "core"}], source_snapshot_id=fixture["snapshot"], score=10,
            payload={"assignment_scores": {"nte-core-17-1": 10}})
        with pytest.raises(ValueError, match="已变化"):
            user.save_replacement_plan_to_slot(fixture["slot"], name="stale", assignments=[{"uid_serial": 2,
                "uid_slot": 17, "kind": "core"}], source_snapshot_id=fixture["snapshot"],
                expected_plan_id=fixture["plan"])
        assert user.get_loadout_slot(fixture["slot"])["current_plan_id"] == new_plan
    actual_connect, changed = sqlite3.connect, []
    class RaceConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql == "BEGIN IMMEDIATE" and not changed:
                changed.append(True)
                errors = []
                def writer():
                    try:
                        with UserDataDao(fixture["account"]) as user:
                            plan = user.save_plan_to_slot(fixture["donor"], name="new donor", assignments=[{
                                "uid_serial": 2, "uid_slot": 17, "kind": "core"}],
                                source_snapshot_id=fixture["snapshot"], score=10,
                                payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "donor",
                                    "assignment_scores": {"nte-core-17-2": 10}})
                            user.set_allocation_lock(plan, True)
                    except Exception as exc:
                        errors.append(exc)
                thread = Thread(target=writer)
                thread.start()
                thread.join(3)
                assert not thread.is_alive() and not errors
            return super().execute(sql, parameters)
    def connect(*args, **kwargs):
        if get_ident() == gui:
            kwargs["factory"] = RaceConnection
        return actual_connect(*args, **kwargs)
    gui = get_ident()
    monkeypatch.setattr(sqlite3, "connect", connect)
    with UserDataDao(fixture["account"]) as user:
        with pytest.raises(ValueError, match="锁定"):
            user.save_replacement_plan_to_slot(fixture["slot"], name="race", assignments=[{
                "uid_serial": 2, "uid_slot": 17, "kind": "core"}], source_snapshot_id=fixture["snapshot"],
                expected_plan_id=new_plan)
        assert changed and user.get_loadout_slot(fixture["slot"])["current_plan_id"] == new_plan
        donor = user.get_loadout_slot(fixture["donor"])["current_plan"]
        assert donor["plan_id"] != fixture["donor_plan"] and donor["allocation_locked"]
        assert donor["assignments"][0]["uid_serial"] == 2


def test_postcommit_read_unit_exit_diagnostic_is_a_committed_receipt(fixture, monkeypatch):
    actual, diagnostics = UserDataDao.save_replacement_plan_to_slot, []
    def intercept(dao, *args, **kwargs):
        result = actual(dao, *args, **kwargs)
        manifest_path = fixture["path"].with_name("manifest.json")
        manifest_path.write_text(manifest_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return result
    monkeypatch.setattr(UserDataDao, "save_replacement_plan_to_slot", intercept)
    detail, target = fixture["detail"], fixture["target"]
    replacement = deepcopy(detail["replacement_items"][1])
    saved = save_official_role_replacement(fixture["account"], detail, target, replacement,
        replacement_score=10, current_assignment_scores={"nte-core-17-1": 10}, postcommit_diagnostic=diagnostics.append)
    assert saved and len(diagnostics) == 1
    with UserDataDao(fixture["account"]) as user:
        assert user.get_loadout_slot(fixture["slot"])["current_plan_id"] == saved
        assert plan_count(fixture["account"]) == 4
    assert StaticCatalogReleaseReader().freeze(fixture["path"]).sha256 == detail["static_release"].sha256


def test_actual_commit_receipt_survives_inventory_refresh_failure(fixture):
    fixture["context"].paths.asset_dir = fixture["context"].paths.role_catalog.asset_root
    refresh_receipts = []
    gui = get_ident()
    def refresh():
        with UserDataDao(fixture["account"]) as user:
            slot = user.get_loadout_slot(fixture["slot"])
            assert slot["current_plan_id"] != fixture["plan"]
            saved = user.get_loadout_plan(slot["current_plan_id"])
            assert saved["assignments"][0]["uid_serial"] == 2
            assert saved["source_snapshot_id"] == fixture["snapshot"]
        refresh_receipts.append((slot["current_plan_id"], get_ident()))
        raise RuntimeError("synthetic postcommit refresh failure")
    fixture["window"]._refresh_equip = refresh
    timer, submitted = automate_confirm(fixture["app"])
    try:
        # 正式库存catch仍保留；这里跑真实worker/SQLite提交，而非mock成功save。
        _optimize_saved_equipment(fixture["window"], "合成角色", "tape", "nte-core-17-1",
                                  plan_id=fixture["plan"])
    finally:
        timer.stop()
    pump(lambda: not any(owner.is_running() for owner in fixture["window"].findChildren(ReplacementTaskOwner)))
    assert len(submitted) == 1 and len(refresh_receipts) == 1
    plan_id, thread = refresh_receipts[0]
    assert thread == gui
    assert plan_count(fixture["account"]) == 4  # 旧2方案+一次真commit新2方案，无回滚或自动重试。
    with UserDataDao(fixture["account"]) as user:
        assert user.get_loadout_slot(fixture["slot"])["current_plan_id"] == plan_id
        assert user.current_inventory_snapshot_id() == fixture["current"]
    assert any(f"配装方案 {plan_id} 已保存" in message and "页面刷新未完成" in message
               for message in fixture["messages"])
    assert not any("读取官方角色详情" in message or "替换失败" in message for message in fixture["messages"])
    print(json.dumps({"actual_saved_plan_id": plan_id, "plan_count_after": 4,
                      "refresh_called_once_on_gui": True, "receipt_retained": True,
                      "ordinary_read_failure_presented": False, "automatic_retry": False}))
