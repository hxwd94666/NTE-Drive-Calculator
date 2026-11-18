# 验证账号目录被句柄占用时导入有界重试、保留原件并给出可操作提示。
from __future__ import annotations

import faulthandler
import os
import sys
from pathlib import Path

import pytest

from src.features.accounts.manager import AccountManager
from src.integrations.accounts import transfer_archive
from src.integrations.accounts.transfer_archive import AccountDirectoryBusyError
from src.services.accounts.transfer import export_account_data, import_account_data
from src.storage.sqlite.user_data_dao import UserDataDao
import src.utils.logger as logger_module

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="目录改名受打开句柄阻止是 Windows 文件语义")


def _manager(root: Path) -> AccountManager:
    manager = AccountManager(root / "data", root / "empty-bundle", lambda _path: [], (), ())
    manager.initialize()
    manager.create_account("A")
    manager.create_account("B")
    manager.set_active_account_id("A")
    return manager


def _name(manager: AccountManager, account_id: str) -> str:
    with UserDataDao(manager.account_dir(account_id) / "user_data.sqlite3") as dao:
        return str(dao.profile()["account_name"])


def _import_leftovers(manager: AccountManager) -> list[Path]:
    return list(manager.accounts_dir.glob(".account-import-*"))


@pytest.fixture
def no_retry_wait(monkeypatch):
    # 保持重试次数，只去掉等待时长，避免测试变慢。
    monkeypatch.setattr(transfer_archive, "DIRECTORY_BUSY_RETRY_DELAYS",
                        (0.0,) * len(transfer_archive.DIRECTORY_BUSY_RETRY_DELAYS))


@windows_only
def test_open_handle_in_target_directory_keeps_original_and_cleans_staging(tmp_path, no_retry_wait):
    manager = _manager(tmp_path)
    database = manager.account_dir("A") / "user_data.sqlite3"
    archive = export_account_data(manager, "A", tmp_path / "account.zip")
    sentinel = manager.account_dir("A") / "scanned_images" / "sentinel.txt"
    sentinel.write_text("current-only", encoding="utf-8")
    before_database = database.read_bytes()
    before_index = manager.accounts_index_file.read_bytes()
    # 复现启动期 faulthandler 一直打开 logs/crash_dump.log 的场景。
    held = open(manager.account_dir("A") / "logs" / "crash_dump.log", "w", encoding="utf-8")
    try:
        with pytest.raises(AccountDirectoryBusyError):
            import_account_data(manager, archive)
        assert manager.accounts_index_file.read_bytes() == before_index
        assert database.read_bytes() == before_database
        assert sentinel.read_text(encoding="utf-8") == "current-only"
        assert _import_leftovers(manager) == []
    finally:
        held.close()
    # 句柄释放后同一入口可直接重试成功，整目录换成导入包内容。
    assert import_account_data(manager, archive) == "A"
    assert not sentinel.exists()
    assert _name(manager, "A") == "A"
    assert _import_leftovers(manager) == []


def test_transient_access_denied_is_retried_then_imported(tmp_path, monkeypatch, no_retry_wait):
    manager = _manager(tmp_path)
    archive = export_account_data(manager, "A", tmp_path / "account.zip")
    target = manager.account_dir("A")
    replace = os.replace
    denied = []

    def busy_twice(source, destination):
        if Path(source) == target and Path(destination).name == "previous-account" and len(denied) < 2:
            denied.append(source)
            raise PermissionError(5, "Access is denied")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", busy_twice)
    assert import_account_data(manager, archive) == "A"
    assert len(denied) == 2
    assert manager.read_index()["active_account_id"] == "A"
    assert _import_leftovers(manager) == []


@windows_only
def test_crash_dump_handle_follows_account_log_directory(tmp_path, monkeypatch):
    enabled = []
    monkeypatch.setattr(faulthandler, "enable", lambda *, file: enabled.append(file))
    monkeypatch.setattr(faulthandler, "disable", lambda: None)
    original_log_dir = logger_module.LOG_DIR
    first = tmp_path / "first" / "logs"
    second = tmp_path / "second" / "logs"
    try:
        logger_module.enable_crash_dump(first)
        with pytest.raises(PermissionError):
            (tmp_path / "first").replace(tmp_path / "first-moved")
        logger_module.set_log_dir(second, reopen_session=False)
        assert Path(enabled[-1].name) == second / "crash_dump.log"
        assert enabled[0].closed
        # 旧账号目录不再被本进程占用（runtime sink 与崩溃转储都已切走）。
        (tmp_path / "first").replace(tmp_path / "first-moved")
    finally:
        logger_module.disable_crash_dump()
        logger_module.set_log_dir(original_log_dir, reopen_session=False)
    assert enabled[-1].closed


def test_dialog_reports_busy_directory_and_restores_original_owner(tmp_path, monkeypatch, no_retry_wait):
    from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox, QPushButton, QWidget

    from src.features.accounts import manager as manager_module
    from tests.support.auto_sync_ui_fixture import application, dispose

    manager = _manager(tmp_path)
    archive = export_account_data(manager, "A", tmp_path / "account.zip")
    before_database = (manager.account_dir("A") / "user_data.sqlite3").read_bytes()
    target = manager.account_dir("A")
    replace = os.replace

    def always_busy(source, destination):
        if Path(source) == target and Path(destination).name == "previous-account":
            raise PermissionError(5, "Access is denied")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", always_busy)
    app = application()
    parent = QWidget()
    owner = ["A"]
    switches = []
    messages = []

    def switch(account_id):
        switches.append(account_id)
        owner[0] = account_id
        manager.set_active_account_id(account_id)
        return True

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(archive), ""))
    for method in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, method,
                            lambda _parent, _title, message, kind=method: messages.append((kind, message)))

    def interact(dialog):
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "导入数据").click()
        app.processEvents()
        return 0

    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        manager_module.show_account_manager_dialog(parent, "", manager, "A", switch, lambda: None)
    finally:
        dispose(parent)
    # 先切到临时 owner 再回切原账号；临时账号清理后列表与原件都不变。
    assert len(switches) == 2 and switches[1] == "A" and owner[0] == "A"
    assert {row["id"] for row in manager.read_index()["accounts"]} == {"default", "A", "B"}
    assert manager.read_index()["active_account_id"] == "A"
    assert (manager.account_dir("A") / "user_data.sqlite3").read_bytes() == before_database
    assert _import_leftovers(manager) == []
    assert len(messages) == 1
    kind, text = messages[0]
    assert kind == "warning"
    assert "仍被占用" in text and "原账号" in text and "下一步" in text
    assert "WinError" not in text and "Access is denied" not in text
