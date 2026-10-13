# 用真实 Qt 工作线程验证鉴定代次、取消、模态确认及物理空闲清理。
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject, QThread
from PySide6.QtWidgets import QApplication

from src.app.page_tasks import close_page_tasks
from src.features.identification.dependencies import IdentificationDependencies
from src.features.identification.task_controller import IdentificationTaskController
from src.models.equipment import Tape
from src.services.identification.tasks import (
    IdentificationImageJob, IdentificationImageRequest, IdentificationMatchRequest,
)

NTE_TEST_TIER = "core"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def spin(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        app.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("worker did not reach expected state")
        time.sleep(.002)
    app.processEvents()


def dependencies(root):
    return IdentificationDependencies("fixture", 1, root, root/"shots", root/"config",
                                     root/"user.sqlite3", Path("config"), root/"static.sqlite3")


def request(deps, uid="fixture"):
    item = Tape(uid=uid, quality="Gold", area=15, set_name="套装",
                main_stats="攻击力%", sub_stats={})
    return IdentificationMatchRequest.freeze(config_dir=deps.config_dir,
        user_database_path=deps.user_database_path, static_database_path=deps.static_database_path,
        items=[item])


@pytest.mark.parametrize("change", ["cancel", "close", "generation"])
def test_stale_delivery_and_physical_close(app, tmp_path, change):
    deps = dependencies(tmp_path)
    current = [deps]
    entered, release = threading.Event(), threading.Event()
    root = QObject()
    def reader(req, **kwargs):
        entered.set()
        assert release.wait(5)
        return req.items[0].uid  # 刻意忽略取消，仍须由交付身份守住。
    owner = IdentificationTaskController(lambda: current[0], root, match_reader=reader)
    delivered, errors = [], []
    owner.matched.connect(lambda *args: delivered.append(args))
    owner.failed.connect(lambda *args: errors.append(args))
    tag = owner.begin(deps)
    try:
        owner.submit_matches(tag, request(deps))
        spin(app, entered.is_set)
        if change == "cancel": owner.cancel()
        elif change == "close": owner.close()
        else: current[0] = replace(deps, generation=2)
        assert owner.has_worker()
        if change == "close": assert close_page_tasks(root)
        release.set()
        spin(app, lambda: not owner.is_running())
        assert not delivered and not errors
        assert not close_page_tasks(root)
    finally:
        release.set(); owner.close(); spin(app, lambda: not owner.has_worker())


def test_latest_serializes_workers_and_delivers_on_gui_thread(app, tmp_path):
    deps = dependencies(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls, delivered, threads = [], [], []
    active = [0, 0]
    def reader(req, **kwargs):
        active[0] += 1; active[1] = max(active)
        try:
            calls.append(req.items[0].uid)
            if len(calls) == 1:
                entered.set(); assert release.wait(5)
            return req.items[0].uid
        finally: active[0] -= 1
    owner = IdentificationTaskController(lambda: deps, match_reader=reader)
    owner.matched.connect(lambda _tag, value: (delivered.append(value), threads.append(QThread.currentThread())))
    try:
        first = owner.begin(deps); owner.submit_matches(first, request(deps, "first"))
        spin(app, entered.is_set)
        second = owner.begin(deps); owner.submit_matches(second, request(deps, "second"))
        third = owner.begin(deps); owner.submit_matches(third, request(deps, "third"))
        assert calls == ["first"]
        release.set(); spin(app, lambda: not owner.is_running())
        assert calls == ["first", "third"] and delivered == ["third"]
        assert active[1] == 1 and threads == [app.thread()]
    finally:
        release.set(); owner.close(); spin(app, lambda: not owner.has_worker())


def test_clipboard_reused_by_latest_is_removed_only_after_physical_idle(app, tmp_path):
    deps = dependencies(tmp_path)
    owned = tmp_path/"identify_clipboard_fixture.png"; owned.write_bytes(b"fixture")
    outside = tmp_path/"outside"; outside.mkdir()
    external = outside/owned.name; external.write_bytes(b"external")
    entered, release = threading.Event(), threading.Event()
    seen = []
    def reader(req, **kwargs):
        seen.append(req.jobs[0].path.exists())
        if len(seen) == 1:
            entered.set(); assert release.wait(5)
            assert owned.exists()
        from src.services.identification.tasks import IdentificationParsedBatch
        return IdentificationParsedBatch((), None)
    owner = IdentificationTaskController(lambda: deps, image_reader=reader)
    owner.parsed.connect(lambda tag, _value: owner.finish(tag))
    image = IdentificationImageRequest(deps.screenshot_dir, tmp_path/"preview.json", deps.config_dir,
        (IdentificationImageJob(owned), IdentificationImageJob(external)))
    try:
        first = owner.begin(deps); owner.submit_images(first, image); spin(app, entered.is_set)
        last = owner.begin(deps); owner.submit_images(last, image)
        assert owned.exists()
        release.set(); spin(app, lambda: not owner.is_running())
        assert seen == [True, True] and not owned.exists()
        assert external.read_bytes() == b"external"
    finally:
        release.set(); owner.close(); spin(app, lambda: not owner.has_worker())


@pytest.mark.parametrize("change", ["cancel", "close", "generation"])
def test_modal_confirmation_rechecks_identity_before_form_or_match(app, tmp_path, change):
    from src.features.identification.controller import IdentificationController
    deps = dependencies(tmp_path); current = [deps]
    tasks = IdentificationTaskController(lambda: current[0])
    tag = tasks.begin(deps)
    def confirm(items):
        assert tasks.is_running()
        if change == "cancel": tasks.cancel()
        elif change == "close": tasks.close()
        else: current[0] = replace(deps, generation=2)
        return True
    owner = SimpleNamespace(_identify_tasks=tasks, _confirm_identify_tape_main_stats=confirm,
                            _load_identify_item_to_form=Mock(), _start_identify_items=Mock())
    IdentificationController._on_identify_items_loaded(owner, tag, [request(deps).items[0]])
    owner._load_identify_item_to_form.assert_not_called()
    owner._start_identify_items.assert_not_called()
    assert not tasks.is_running()
    tasks.close()


def test_clear_keeps_start_disabled_until_physical_worker_finishes(app, tmp_path):
    from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget
    from src.features.identification.controller import IdentificationController
    deps = dependencies(tmp_path)
    account = SimpleNamespace(active_account_id=deps.account_id,
        account_data_root=deps.account_data_root, screenshot_dir=deps.screenshot_dir,
        user_config_dir=deps.user_config_dir, user_database_path=deps.user_database_path)
    context = SimpleNamespace(account=account, generation=deps.generation,
        paths=SimpleNamespace(config_dir=deps.config_dir,
                              equipment_allocation_database_path=deps.static_database_path))
    parent = QWidget()
    owner = IdentificationController(app_context=context, dialog_parent=parent,
        card_factory=lambda: None, equipment_presentation=None,
        hotkey_manager=SimpleNamespace(active_owner=None, stop=Mock()),
        minimize_window=lambda: None, restore_window=lambda: None, activate_window=lambda: None)
    owner.ident_parse_btn, owner.ident_manual_btn = QPushButton(), QPushButton()
    owner.ident_path_edit, owner.ident_manual_text = QLineEdit(), QTextEdit()
    owner.ident_summary, owner.ident_result_layout = QLabel(), QVBoxLayout()
    owner._refresh_identify_previews = Mock()  # 只隔离缩略图绘制，保留真实输入/按钮与 owner。
    entered, release = threading.Event(), threading.Event()
    def reader(req, **kwargs):
        entered.set(); assert release.wait(5)
        return []
    owner._identify_tasks._match_reader = reader
    tag = owner._identify_tasks.begin(deps)
    owner._identify_task, owner._identify_dependencies = tag, deps
    owner.ident_path_edit.setText("old.png"); owner.ident_manual_text.setPlainText("old input")
    try:
        owner._identify_tasks.submit_matches(tag, request(deps))
        spin(app, entered.is_set)
        owner._clear_identify_input()
        assert not owner.ident_path_edit.text() and not owner.ident_manual_text.toPlainText()
        assert not owner.ident_parse_btn.isEnabled() and not owner.ident_manual_btn.isEnabled()
        assert owner.is_running()
        release.set(); spin(app, lambda: not owner.is_running())
        assert owner.ident_parse_btn.isEnabled() and owner.ident_manual_btn.isEnabled()
        assert owner.ident_summary.text() == "等待输入装备数据"
    finally:
        release.set(); owner.close(); spin(app, lambda: not owner.is_running())
        parent.deleteLater(); app.processEvents()



def test_context_switch_preserves_identification_and_scanning_task_dependencies(tmp_path):
    from dataclasses import replace
    from src.app.context import AccountContext, AppContext, ApplicationPaths
    from src.features.identification.dependencies import current_identification_dependencies, task_identification_dependencies
    from src.features.scanning.dependencies import ScanningDependencies

    paths = ApplicationPaths.from_roots(root=tmp_path, app_dir=tmp_path,
        data_root=tmp_path, bundled_config_dir=tmp_path/"config", asset_dir=tmp_path/"assets",
        app_icon_path=tmp_path/"app.ico")
    first = AccountContext("first", "fixture", tmp_path/"first", tmp_path/"first/user.sqlite3",
                           tmp_path/"first/config", tmp_path/"first/screenshots", tmp_path/"first/logs")
    context = AppContext(paths, first)
    pinned = IdentificationDependencies.from_app_context(context)
    scanning = ScanningDependencies.from_app_context(context)
    host = SimpleNamespace(app_context=context, _identify_dependencies=pinned)
    second = replace(first, active_account_id="second", user_database_path=tmp_path/"second/user.sqlite3")
    context.switch_account(second)
    assert task_identification_dependencies(host) is pinned
    assert current_identification_dependencies(host).account_id == "second"
    assert pinned.account_id == scanning.account_id == "first"
    assert pinned.generation == scanning.generation < context.generation
    assert pinned.user_database_path == scanning.user_database_path == first.user_database_path
    assert pinned.user_database_path != context.account.user_database_path
