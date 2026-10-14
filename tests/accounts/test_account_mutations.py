# 用合成账号验证创建与删除的索引、正式身份、目录发布和失败后的数据保全。
from __future__ import annotations

from pathlib import Path

import pytest

from src.features.accounts import manager as manager_module
from src.features.accounts.manager import AccountManager
from src.integrations.accounts.mutation_directories import AccountMutationRecoveryError, retired_account_directory
from src.storage.sqlite.user_data_dao import UserDataDao


def manager_at(root: Path) -> AccountManager:
    manager = AccountManager(root / "runtime", root / "bundle", lambda _path: [], (), ())
    manager.initialize()
    return manager


@pytest.mark.parametrize("failure", ["database", "index", "occupied", "success"])
def test_create_publishes_only_complete_new_identity(tmp_path, monkeypatch, failure):
    manager = manager_at(tmp_path)
    before_index = manager.accounts_index_file.read_bytes()
    before_default = (manager.account_dir("default") / "user_data.sqlite3").read_bytes()
    occupied = manager.account_dir("new") / "user_data.sqlite3"
    if failure == "occupied":
        with UserDataDao(occupied, account_id="foreign", account_name="foreign"):
            pass
        before_occupied = occupied.read_bytes()

    factory = manager_module.UserDataDao

    def create_database(path, **identity):
        if failure == "database" and identity.get("account_id") == "new":
            with factory(path, **identity):
                pass
            raise OSError("SYNTHETIC_DATABASE_CREATE_FAILURE")
        return factory(path, **identity)

    publish = manager.write_index

    def publish_index(data):
        if failure == "index" and any(row["id"] == "new" for row in data["accounts"]):
            raise OSError("SYNTHETIC_INDEX_PUBLICATION_FAILURE")
        publish(data)

    monkeypatch.setattr(manager_module, "UserDataDao", create_database)
    monkeypatch.setattr(manager, "write_index", publish_index)
    if failure in {"database", "index"}:
        with pytest.raises(OSError):
            manager.create_account("new")
        assert manager.accounts_index_file.read_bytes() == before_index
        assert not manager.account_dir("new").exists()
    else:
        created = manager.create_account("new")
        assert created == ("new_2" if failure == "occupied" else "new")
        with UserDataDao(manager.account_dir(created) / "user_data.sqlite3") as dao:
            assert dao.profile()["account_id"] == created
            assert dao.profile()["account_name"] == "new"
        assert manager.read_index()["active_account_id"] == "default"
        if failure == "occupied":
            assert occupied.read_bytes() == before_occupied
    assert (manager.account_dir("default") / "user_data.sqlite3").read_bytes() == before_default


@pytest.mark.parametrize("failure", ["index", "move", "cleanup", "success"])
def test_delete_separates_index_commit_from_retired_directory_cleanup(tmp_path, monkeypatch, failure):
    manager = manager_at(tmp_path)
    victim = manager.create_account("victim")
    target = manager.account_dir(victim)
    (target / "sentinel.txt").write_bytes(b"synthetic-account-owned")
    before_index = manager.accounts_index_file.read_bytes()
    before_database = (target / "user_data.sqlite3").read_bytes()
    before_default = (manager.account_dir("default") / "user_data.sqlite3").read_bytes()
    publish = manager.write_index

    def publish_index(data):
        if failure == "index" and not any(row["id"] == victim for row in data["accounts"]):
            raise OSError("SYNTHETIC_DELETE_INDEX_FAILURE")
        publish(data)

    remove = manager_module.shutil.rmtree

    def remove_directory(path, *args, **kwargs):
        directory = Path(path)
        if failure == "cleanup" and (directory / "sentinel.txt").exists():
            # 模拟 rmtree 已经处理部分文件才遇占用；不得对仍登记的活动目录这样做。
            (directory / "sentinel.txt").unlink()
            raise OSError("SYNTHETIC_PARTIAL_CLEANUP_FAILURE")
        return remove(path, *args, **kwargs)

    monkeypatch.setattr(manager, "write_index", publish_index)
    monkeypatch.setattr(manager_module.shutil, "rmtree", remove_directory)
    rename = Path.rename

    def move_directory(source, destination):
        if failure == "move" and source == target:
            raise OSError("SYNTHETIC_DIRECTORY_BUSY")
        return rename(source, destination)

    monkeypatch.setattr(Path, "rename", move_directory)
    if failure in {"index", "move"}:
        with pytest.raises(OSError):
            manager.delete_account(victim)
        assert manager.accounts_index_file.read_bytes() == before_index
        assert (target / "user_data.sqlite3").read_bytes() == before_database
        assert (target / "sentinel.txt").read_bytes() == b"synthetic-account-owned"
    else:
        result = manager.delete_account(victim)
        assert all(row["id"] != victim for row in manager.read_index()["accounts"])
        assert not target.exists()
        if failure == "cleanup":
            pending = result.cleanup_pending
            assert pending.parent == manager.accounts_dir
            assert (pending / "user_data.sqlite3").read_bytes() == before_database
        else:
            assert result.cleanup_pending is None
    assert (manager.account_dir("default") / "user_data.sqlite3").read_bytes() == before_default


def test_delete_restore_failure_preserves_original_and_blocks_empty_reinitialization(tmp_path, monkeypatch):
    manager = manager_at(tmp_path)
    identity = manager.create_account("victim")
    target = manager.account_dir(identity)
    retired = retired_account_directory(target)
    database = (target / "user_data.sqlite3").read_bytes()
    index = manager.accounts_index_file.read_bytes()
    rename = Path.rename

    def fail_restore(source, destination):
        if source == retired:
            raise OSError("SYNTHETIC_RESTORE_BUSY")
        return rename(source, destination)

    monkeypatch.setattr(Path, "rename", fail_restore)
    monkeypatch.setattr(manager, "write_index", lambda _data: (_ for _ in ()).throw(OSError("SYNTHETIC_INDEX_FAILURE")))
    with pytest.raises(AccountMutationRecoveryError):
        manager.delete_account(identity)
    assert manager.accounts_index_file.read_bytes() == index
    assert (retired / "user_data.sqlite3").read_bytes() == database
    for action in (lambda: manager.activate(identity), lambda: manager.seed_account_data(identity)):
        with pytest.raises(AccountMutationRecoveryError):
            action()
        assert not target.exists()
    # 只恢复本测试的合成目录，证明解除占用后可由同一正式入口重试。
    monkeypatch.undo()
    retired.rename(target)
    assert manager.delete_account(identity).cleanup_pending is None


def test_create_compensation_does_not_remove_a_replaced_directory(tmp_path, monkeypatch):
    manager = manager_at(tmp_path)
    index = manager.accounts_index_file.read_bytes()
    target = manager.account_dir("new")
    original = tmp_path / "preserved-new"

    def interrupted_publish(_data):
        target.rename(original)
        with UserDataDao(target / "user_data.sqlite3", account_id="foreign", account_name="foreign"):
            pass
        raise OSError("SYNTHETIC_INDEX_FAILURE_WITH_DIRECTORY_REPLACEMENT")

    monkeypatch.setattr(manager, "write_index", interrupted_publish)
    with pytest.raises(AccountMutationRecoveryError):
        manager.create_account("new")
    assert manager.accounts_index_file.read_bytes() == index
    for folder, identity in ((original, "new"), (target, "foreign")):
        with UserDataDao(folder / "user_data.sqlite3") as dao:
            assert dao.profile()["account_id"] == identity


@pytest.mark.parametrize("state", ["cleanup-pending", "recovery-blocked"])
def test_import_distinguishes_committed_deletion_from_unrestored_original(tmp_path, monkeypatch, state):
    from src.integrations.accounts import mutation_directories
    from src.services.accounts.transfer import export_account_data, import_account_data

    manager = manager_at(tmp_path)
    identity = manager.create_account("victim")
    target = manager.account_dir(identity)
    archive = export_account_data(manager, identity, tmp_path / "synthetic.zip")
    original = (target / "user_data.sqlite3").read_bytes()
    retired = retired_account_directory(target)
    with monkeypatch.context() as failures:
        if state == "cleanup-pending":
            failures.setattr(mutation_directories, "_remove_owned_directory",
                             lambda *_args: (_ for _ in ()).throw(OSError("SYNTHETIC_CLEANUP_PENDING")))
            assert manager.delete_account(identity).cleanup_pending == retired
        else:
            rename = Path.rename

            def restore_fails(source, destination):
                if source == retired:
                    raise OSError("SYNTHETIC_RESTORE_BUSY")
                return rename(source, destination)

            failures.setattr(Path, "rename", restore_fails)
            failures.setattr(manager, "write_index",
                             lambda _data: (_ for _ in ()).throw(OSError("SYNTHETIC_INDEX_FAILURE")))
            with pytest.raises(AccountMutationRecoveryError):
                manager.delete_account(identity)
    index = manager.accounts_index_file.read_bytes()
    folders = set(manager.accounts_dir.iterdir())
    released = []
    if state == "recovery-blocked":
        with pytest.raises(AccountMutationRecoveryError):
            import_account_data(manager, archive, before_replace=released.append)
        assert manager.accounts_index_file.read_bytes() == index
        assert not target.exists()
        assert (retired / "user_data.sqlite3").read_bytes() == original
        for action in (lambda: manager.activate(identity), lambda: manager.seed_account_data(identity)):
            with pytest.raises(AccountMutationRecoveryError):
                action()
        assert not target.exists()
    else:
        assert import_account_data(manager, archive, before_replace=released.append) == identity
        assert manager.read_index()["active_account_id"] == identity
        assert manager.activate(identity).active_account_id == identity
        assert manager.initialize().active_account_id == identity
        retained = set(manager.accounts_dir.iterdir()) - folders - {target}
        assert len(retained) == 1
        assert (retained.pop() / "user_data.sqlite3").read_bytes() == original
        assert not retired.exists()
    assert released == []


def test_delete_rejects_index_path_alias_instead_of_deleting_another_account(tmp_path):
    manager = manager_at(tmp_path)
    alias = "../accounts/default"
    index = manager.read_index()
    index["accounts"].append({"id": alias, "name": "synthetic-alias"})
    manager.write_index(index)
    before_index = manager.accounts_index_file.read_bytes()
    before_database = (manager.account_dir("default") / "user_data.sqlite3").read_bytes()
    with pytest.raises(ValueError, match="invalid_account_id"):
        manager.delete_account(alias)
    assert manager.accounts_index_file.read_bytes() == before_index
    assert (manager.account_dir("default") / "user_data.sqlite3").read_bytes() == before_database


@pytest.mark.parametrize("action", ["create-failure", "delete-cleanup"])
def test_dialog_reports_uncommitted_creation_and_committed_cleanup_separately(tmp_path, monkeypatch, action):
    from auto_sync_ui_fixture import application, dispose
    from PySide6.QtWidgets import QComboBox, QDialog, QInputDialog, QMessageBox, QPushButton, QWidget

    app = application()
    manager = manager_at(tmp_path)
    manager.create_account("victim")
    parent = QWidget()
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, message: messages.append((title, message)))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.Yes)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args: ("new", True))
    if action == "create-failure":
        monkeypatch.setattr(manager_module, "UserDataDao", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("SYNTHETIC_DATABASE_FAILURE")))
    else:
        monkeypatch.setattr(manager_module.shutil, "rmtree", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("SYNTHETIC_DIRECTORY_BUSY")))

    def interact(dialog):
        combo = dialog.findChild(QComboBox)
        combo.setCurrentIndex(combo.findData("victim"))
        label = "新建" if action == "create-failure" else "删除"
        next(button for button in dialog.findChildren(QPushButton) if button.text() == label).click()
        app.processEvents()
        assert len(messages) == 1
        if action == "create-failure":
            assert "新建账号未完成" in messages[0][0]
            assert combo.findData("new") == -1 and combo.findData("victim") >= 0
        else:
            assert "账号已移除，文件清理待完成" in messages[0][0]
            assert combo.findData("victim") == -1 and combo.currentData() == "default"
        return 0

    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        manager_module.show_account_manager_dialog(parent, "", manager, "default", lambda _identity: False, lambda: None)
        assert manager.read_index()["active_account_id"] == "default"
    finally:
        dispose(parent)
