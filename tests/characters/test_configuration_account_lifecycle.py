# 验证正式账号切换接口阻止提交跨代次并丢弃旧账号读取。
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

NTE_TEST_TIER = "core"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.app.context import AccountContext, AppContext, ApplicationPaths
from src.features.configuration.controller import BasicWeightController
from src.features.configuration.dependencies import freeze_basic_weight_dependencies
from src.storage.sqlite.user_data_dao import UserDataDao
from src.ui.controllers.configuration_controller import register_page_configuration_lifecycle


def wait(predicate):
    until = time.monotonic() + 4
    while not predicate() and time.monotonic() < until:
        QTest.qWait(5)
    assert predicate()


@pytest.fixture
def context(tmp_path):
    app = QApplication.instance() or QApplication([])
    root = Path(__file__).resolve().parents[2]
    paths = ApplicationPaths.from_roots(
        root=root, app_dir=root, data_root=tmp_path, bundled_config_dir=root / "config",
        asset_dir=root / "assets", app_icon_path=root / "assets/app_icon.ico",
        static_database_path=root / "data/game_static.sqlite3",
    )
    accounts = []
    for name in ("first", "second"):
        base = tmp_path / name
        account = AccountContext(name, name, base, base / "user.sqlite3", base / "config", base / "screenshots", base / "logs")
        with UserDataDao(account.user_database_path, account_id=name):
            pass
        accounts.append(account)
    ctx = AppContext(paths, accounts[0])
    controller = BasicWeightController(freeze_basic_weight_dependencies(ctx))
    unregister = register_page_configuration_lifecycle(ctx, lambda: (controller,))
    yield ctx, accounts[1], controller
    controller.close()
    wait(lambda: not controller.is_writing() and not controller._reads.is_running())
    unregister()
    app.processEvents()


def test_public_account_switch_keeps_original_context_until_commit_settles(context):
    ctx, target, controller = context
    release, accepted = threading.Event(), []
    controller.submit_change(lambda: release.wait(2), accepted.append, pytest.fail, pytest.fail)
    with pytest.raises(RuntimeError, match="正在提交"):
        ctx.switch_account(target)
    assert ctx.account.active_account_id == "first" and ctx.generation == 0
    release.set()
    wait(lambda: not controller.is_writing())
    ctx.switch_account(target)
    assert ctx.account.active_account_id == "second" and ctx.generation == 1
    assert len(accepted) == 1


def test_public_switch_cancels_old_read_delivery_without_waiting_on_ui(context, monkeypatch):
    ctx, target, controller = context
    release, entered, applied = threading.Event(), threading.Event(), []
    def read():
        entered.set()
        release.wait(2)
        return {}
    monkeypatch.setattr(controller, "load_form_data", read)
    controller.request_form_data(applied.append, pytest.fail)
    wait(entered.is_set)
    ctx.switch_account(target)
    assert ctx.generation == 1 and controller._reads.is_running()
    release.set()
    wait(lambda: not controller._reads.is_running())
    assert applied == []


def test_public_switch_clears_independent_page_before_old_read_returns(context, monkeypatch):
    from src.features.configuration.page_owner import BasicWeightPage
    from PySide6.QtWidgets import QLabel

    ctx, target, _existing_controller = context
    page = BasicWeightPage(ctx, lambda _key: None, lambda: None)
    owner = page.controller()
    unregister = register_page_configuration_lifecycle(
        ctx, lambda: (page.task_owner,), after_stop=page.clear_projection,
    )
    release, entered = threading.Event(), threading.Event()
    def read():
        entered.set()
        release.wait(2)
        return {}
    monkeypatch.setattr(owner, "load_form_data", read)
    page._config_dirty = True
    page._config_form_data = {"1": {"character_id": 1, "name_zh": "旧账号夹具"}}
    page.config_form_layout.addWidget(QLabel("旧账号夹具"))
    owner.request_form_data(lambda _value: pytest.fail("旧账号读取投影到了新页面"), pytest.fail)
    wait(entered.is_set)
    ctx.switch_account(target)
    assert ctx.generation == 1 and owner._closed and owner._reads.is_running()
    assert not page.is_dirty() and page._config_form_data is None
    assert page.config_form_layout.count() == 0
    release.set()
    wait(lambda: not owner._reads.is_running())
    replacement = page.controller()
    assert replacement.dependencies.account_id == "second" and replacement is not owner
    page.close_tasks()
    unregister()
    page.close()
    page.deleteLater()
    QApplication.processEvents()
