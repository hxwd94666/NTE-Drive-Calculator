# 用真实账号索引和用户库验证切换失败、补偿代次与自动服务的提交边界。
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMessageBox, QWidget

from auto_sync_ui_fixture import application, dispose
from src.app.context import AppContext, AccountSwitchError, CallbackAccountLifecycle
from src.domain.work_mode import WorkMode
from src.features.accounts.manager import AccountManager
from src.ui.app import MainWindow
from src.ui.work_mode_composition import initialize_mode_policy


def test_switch_rejects_running_rewind_before_target_lookup(monkeypatch):
    application()
    host = QWidget()
    host.app_context = SimpleNamespace(
        account=SimpleNamespace(active_account_id="old"), generation=0,
    )
    host._equipment_assembly_is_running = lambda: False
    # 物理 running 的真实性由倒带 owner 回归覆盖；这里验证组合根不漏接该门禁。
    host.toolbox_page = SimpleNamespace(is_rewind_running=lambda: True)
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: messages.append(True))
    try:
        assert MainWindow._switch_account(host, "new") is False
        assert messages == [True]
        assert host.app_context.account.active_account_id == "old"
        assert host.app_context.generation == 0
    finally:
        dispose(host)


@pytest.mark.parametrize("failure", ["settings", "stop", "rebuild", "notify", "publish", "resume", "rollback", "success"])
def test_switch_result_keeps_account_settings_index_and_automatic_owner_together(tmp_path, monkeypatch, failure):
    application()
    manager = AccountManager(tmp_path, tmp_path / "bundle", lambda _path: [], (), ())
    old = manager.initialize()
    target_id = manager.create_account("target")
    target = manager.activate(target_id)
    original = {item.active_account_id: item.user_database_path.read_bytes() for item in (old, target)}
    index = manager.accounts_index_file.read_bytes()
    trace = []

    def settings(database_path, **_kwargs):
        if failure == "settings" and database_path == target.user_database_path:
            raise OSError("SYNTHETIC_SETTINGS_FAILURE")
        return SimpleNamespace(database_path=database_path)

    context = AppContext(SimpleNamespace(config_dir=tmp_path / "config"), old, settings_factory=settings)
    host = QWidget()
    host.app_context = context
    host._account_manager = manager
    host._equipment_assembly_is_running = lambda: False
    host.toolbox_page = SimpleNamespace(is_rewind_running=lambda: False)
    idle = SimpleNamespace(is_running=lambda: False)
    host.scanning_controller = host.identification_controller = host.battle_report_controller = idle
    host.native_plugin_update_controller = idle
    host.equipment_persistence_controller = SimpleNamespace(
        request_stop=lambda: True, is_running=lambda: False, rebuild=lambda: None,
    )
    host._inventory_sync_service = None
    host._maybe_auto_start_inventory_sync = lambda: trace.append(("automatic", context.account.active_account_id))
    host.auto_sync_controller = SimpleNamespace(close=lambda: trace.append(("automatic-close",)))
    initialize_mode_policy(host)
    policy = host.work_mode_service
    policy.select_mode(WorkMode.MEDIUM, risk_confirmed=True)
    host.work_mode_service = policy
    host.page_identity = (old.active_account_id, old.user_database_path)

    def stop():
        trace.append(("stop", context.account.active_account_id, context.generation))
        if failure == "stop":
            raise RuntimeError("SYNTHETIC_STOP_PENDING")

    def rebuild(current):
        assert current.user_database_path == context.account_settings.database_path
        trace.append(("rebuild", current.active_account_id, context.generation))
        if failure == "rollback" or (failure == "rebuild" and current == target):
            raise RuntimeError("SYNTHETIC_REBUILD_FAILURE")

    def notify(event):
        host.page_identity = (event.current.active_account_id, context.account_settings.database_path)
        trace.append(("projection", event.current.active_account_id, event.generation))
        if failure == "notify" and event.current == target:
            raise RuntimeError("SYNTHETIC_PROJECTION_FAILURE")

    def start():
        trace.append(("resume", context.account.active_account_id))
        # 自动 owner 启动时索引必须已经发布，回滚时只能恢复旧账号。
        assert manager.read_index()["active_account_id"] == context.account.active_account_id
        if failure == "resume":
            raise RuntimeError("SYNTHETIC_RESUME_FAILURE")

    context.register_account_lifecycle(CallbackAccountLifecycle(
        is_running=lambda: True, stop=stop, rebuild=rebuild, start=start,
    ))
    context.subscribe_account_changed(notify)
    publish = manager.set_active_account_id

    def publish_index(identity):
        trace.append(("publish", identity))
        if failure == "publish":
            raise OSError("SYNTHETIC_INDEX_FAILURE")
        publish(identity)

    monkeypatch.setattr(manager, "set_active_account_id", publish_index)
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, message: messages.append((title, message)))
    # 安全暂停的落盘失败也不能恢复已经撤销的内存权限。
    if failure == "rollback":
        monkeypatch.setattr(policy._store, "save", lambda _settings: (_ for _ in ()).throw(OSError("SYNTHETIC_DISK_FULL")))
    try:
        committed = MainWindow._switch_account(host, target_id)
        expected = target if failure in {"resume", "success"} else old
        assert committed is (expected == target)
        assert context.account == expected
        assert context.account_settings.database_path == expected.user_database_path
        assert manager.read_index()["active_account_id"] == expected.active_account_id
        assert context.generation == (0 if failure in {"settings", "stop"} else 1 if committed else 2)
        if not committed:
            assert manager.accounts_index_file.read_bytes() == index
        if failure == "settings":
            assert trace == []
        if failure == "rollback":
            assert not host.isEnabled()
            assert policy.settings.paused and not policy.allowed("native_sync", automatic=True)
            # 持有初始 guard 的真实会话同样受冻结，不仅是 QWidget 禁用。
            with pytest.raises(AccountSwitchError):
                host.operation_guard("native_sync")
            with pytest.raises(AccountSwitchError):
                host.native_game_session.read_performance_status()
            assert host.native_game_session.configure_performance(
                {"enabled": False}, (context.account.active_account_id, context.generation),
            )["enabled"] is False
            host.native_game_session.close()
            assert ("automatic-close",) in trace
            with pytest.raises(AccountSwitchError, match="访问已暂停"):
                context.create_user_dao()
            with pytest.raises(AccountSwitchError):
                context.switch_account(target)
        else:
            assert host.isEnabled()
            assert host.page_identity == (expected.active_account_id, expected.user_database_path)
            with context.create_user_dao() as dao:
                assert dao.profile()["account_id"] == expected.active_account_id
        assert ("automatic", target_id) in trace if failure == "success" else ("automatic", target_id) not in trace
        for item in (old, target):
            assert item.user_database_path.read_bytes() == original[item.active_account_id]
        print({"phase": failure, "committed": committed, "generation": context.generation,
               "owner": context.account.active_account_id, "trace": trace, "messages": messages})
    finally:
        dispose(host)
