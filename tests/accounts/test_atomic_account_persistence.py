# 以真实 SQLite、原子文件故障和 Event 线程验证账号迁移与条件补偿。
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

from src.features.accounts import manager as manager_module
from src.features.accounts.manager import AccountManager
from src.services.accounts.transfer import export_account_data, import_account_data
from src.integrations.accounts import index_file
from src.integrations.accounts.transfer_archive import AccountImportRecoveryError
from src.services.accounts import naming as naming_module
from src.services.accounts.naming import AccountNamingService
from src.services.accounts.settings import AccountSettingsService
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.static_game_data_metadata import MINIMUM_SUPPORTED_SCHEMA_VERSION
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataError, UserDataValidationError


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _settings_static(path: Path) -> None:
    schema = Path(__file__).resolve().parents[2] / "src/storage/sqlite/schema/013_game_static_setting_defaults.sql"
    # 本夹具只有设置 SQL 表面，不宣称完整战斗 dataset。
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE schema_migration(version INTEGER PRIMARY KEY, applied_at_utc TEXT)")
        connection.execute("INSERT INTO schema_migration VALUES (?, 'synthetic')",
                           (max(13, MINIMUM_SUPPORTED_SCHEMA_VERSION),))
        connection.executescript(schema.read_text(encoding="utf-8"))


def _setting_state(connection: sqlite3.Connection) -> dict:
    return {"copies": {key: json.loads(value) for key, value in connection.execute(
        "SELECT setting_key, value_json FROM application_setting_copy ORDER BY setting_key")},
        "marker": connection.execute("SELECT 1 FROM application_setting_migration").fetchone() is not None}


def _deferred_failure(connection: sqlite3.Connection, target: str, action: str) -> None:
    # SQL 语句可完成，DEFERRABLE 约束只在真实 connection.commit() 处失败。
    connection.executescript("""
        CREATE TABLE fault_parent(id INTEGER PRIMARY KEY);
        CREATE TABLE fault_child(id INTEGER REFERENCES fault_parent(id) DEFERRABLE INITIALLY DEFERRED);
    """ + f"CREATE TRIGGER fault_commit AFTER {action} ON {target} BEGIN INSERT INTO fault_child VALUES (999); END;")
    connection.commit()


@pytest.mark.parametrize("failure", (
    "success", "later-group", "marker", "commit", "normalize", "concurrent-explicit", "concurrent-marker",
))
def test_settings_migration_is_one_missing_only_transaction(tmp_path, monkeypatch, failure):
    static = tmp_path / "settings.sqlite3"
    _settings_static(static)
    static_hash = _sha(static)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    values = {"hotkeys.json": {"capture": "F6", "finish": "F7", "stop": "F8"},
              "update_config.json": {"never_remind": True, "ignored_version": "synthetic"},
              "ui_preferences.json": {"log_enabled": True}}
    if failure == "normalize":
        values["hotkeys.json"]["stop"] = ""
    for filename, value in values.items():
        (legacy / filename).write_text(json.dumps(value), encoding="utf-8")
    legacy_hashes = {path.name: _sha(path) for path in legacy.iterdir()}
    database = tmp_path / "user.sqlite3"
    with UserDataDao(database, account_id="settings", account_name="settings"):
        pass
    with closing(sqlite3.connect(database)) as setup:
        if failure == "later-group":
            setup.execute("""CREATE TRIGGER fault_group BEFORE INSERT ON application_setting_copy
                             WHEN NEW.setting_key='update' BEGIN SELECT RAISE(ABORT, 'GROUP_ABORT'); END""")
        elif failure == "marker":
            setup.execute("""CREATE TRIGGER fault_marker BEFORE INSERT ON application_setting_migration
                             BEGIN SELECT RAISE(ABORT, 'MARKER_ABORT'); END""")
        elif failure == "commit":
            _deferred_failure(setup, "application_setting_migration", "INSERT")
        setup.commit()
    service = AccountSettingsService(database, static_database_path=static, legacy_config_dir=legacy)
    reads = []
    original_reader = service._read_legacy_json

    def reader(path):
        reads.append(path.name)
        value = original_reader(path)
        if path.name == "hotkeys.json" and failure.startswith("concurrent-"):
            with UserDataDao(database) as peer:
                peer.replace_application_setting_copy("hotkeys", {"capture": "F2"})
                if failure == "concurrent-marker":
                    peer.mark_legacy_application_settings_imported()
        return value

    monkeypatch.setattr(service, "_read_legacy_json", reader)
    with closing(sqlite3.connect(database)) as observer:
        before = _setting_state(observer)
        expected_error = UserDataValidationError if failure == "normalize" else sqlite3.IntegrityError
        if failure in {"later-group", "marker", "commit", "normalize"}:
            with pytest.raises(expected_error) as caught:
                service.migrate_legacy_settings()
            assert _setting_state(observer) == before
            with UserDataDao(database) as reopened:
                assert reopened.list_application_setting_copies() == before["copies"]
                assert reopened.legacy_application_settings_imported() == before["marker"]
            print(json.dumps({"scenario": "settings-" + failure, "error": str(caught.value),
                              "immediate_observer": _setting_state(observer), "new_dao_equal": True}))
            if failure != "normalize":
                with closing(sqlite3.connect(database)) as setup, setup:
                    setup.execute("DROP TRIGGER " + {"later-group": "fault_group", "marker": "fault_marker",
                                                      "commit": "fault_commit"}[failure])
                service.migrate_legacy_settings()
                assert _setting_state(observer)["marker"]
                assert set(_setting_state(observer)["copies"]) == {"hotkeys", "update", "ui"}
        else:
            service.migrate_legacy_settings()
            state = _setting_state(observer)
            assert state["marker"]
            if failure == "concurrent-marker":
                assert state["copies"] == {"hotkeys": {"capture": "F2"}}
            else:
                assert set(state["copies"]) == {"hotkeys", "update", "ui"}
                if failure == "concurrent-explicit":
                    assert state["copies"]["hotkeys"] == {"capture": "F2"}
            before_repeat = state
            service.migrate_legacy_settings()
            assert _setting_state(observer) == before_repeat
            print(json.dumps({"scenario": "settings-" + failure, "state": state, "idempotent": True}))
    expected_reads = 2 if failure in {"later-group", "marker", "commit"} else 1
    assert reads.count("hotkeys.json") == expected_reads
    assert len(reads) == 3 * expected_reads
    StaticGameDataDao.close_shared_connections(static)
    assert _sha(static) == static_hash
    assert legacy_hashes == {path.name: _sha(path) for path in legacy.iterdir()}


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


def _peer_change(manager: AccountManager) -> None:
    manager.rename_account("B", "B-new")
    manager.create_account("C")
    manager.set_active_account_id("B")


@pytest.mark.parametrize("failure", ("guard", "file", "commit", "commit-conflict", "compensation-failure"))
def test_naming_failure_only_compensates_owned_index(tmp_path, monkeypatch, failure):
    manager = _manager(tmp_path)
    database = manager.account_dir("A") / "user_data.sqlite3"
    original = manager.accounts_index_file.read_bytes()
    guard_calls = 0

    def guard():
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 2 and failure == "guard":
            _peer_change(manager)
            return False
        return True

    service = AccountNamingService(accounts_index_path=manager.accounts_index_file,
                                  user_database_path=database, account_id="A", context_is_current=guard)
    if failure.startswith("commit") or failure == "compensation-failure":
        with closing(sqlite3.connect(database)) as setup:
            _deferred_failure(setup, "database_profile", "UPDATE")
    replace = index_file.os.replace
    injected = False

    def failing_replace(source, target):
        nonlocal injected
        raw = Path(source).read_bytes()
        if not injected and Path(target) == manager.accounts_index_file:
            names = {row["id"]: row["name"] for row in json.loads(raw)["accounts"]}
            if failure == "file" and names.get("A") == "A-new":
                injected = True
                _peer_change(manager)
                raise OSError("SYNTHETIC_REPLACE_FAILURE")
            if failure == "compensation-failure" and names.get("A") == "A":
                injected = True
                raise OSError("SYNTHETIC_COMPENSATION_FAILURE")
        return replace(source, target)

    monkeypatch.setattr(index_file.os, "replace", failing_replace)
    if failure == "commit-conflict":
        writer = service._write_bytes_atomic

        def publish_then_peer(raw):
            writer(raw)
            _peer_change(manager)

        monkeypatch.setattr(service, "_write_bytes_atomic", publish_then_peer)
    with closing(sqlite3.connect(database)) as observer:
        with pytest.raises((UserDataError, sqlite3.IntegrityError)) as caught:
            service.rename("A-new")
        assert observer.execute("SELECT account_name FROM database_profile").fetchone()[0] == "A"
        assert _name(manager, "A") == "A"
        current = manager.read_index()
        names = {row["id"]: row["name"] for row in current["accounts"]}
        if failure == "commit":
            assert manager.accounts_index_file.read_bytes() == original
        elif failure in {"guard", "file", "commit-conflict"}:
            assert names["B"] == "B-new" and "C" in names and current["active_account_id"] == "B"
            assert _name(manager, "B") == "B-new"
        if failure in {"guard", "file", "commit"}:
            assert names["A"] == "A"
        else:
            assert names["A"] == "A-new"
            assert "补偿冲突" in str(caught.value) if failure == "commit-conflict" else "补偿未完成" in str(caught.value)
        assert not tuple(manager.accounts_dir.glob("*.tmp"))
        print(json.dumps({"scenario": "naming-" + failure, "error": str(caught.value),
                          "index": current, "observer_name": "A", "new_dao_name": _name(manager, "A")}, ensure_ascii=False))


@pytest.mark.parametrize("writer", ("initialize", "create", "delete", "active", "seed", "rename", "write"))
def test_account_writers_wait_for_naming_index_lock(tmp_path, monkeypatch, writer):
    manager = _manager(tmp_path)
    acquired = threading.Event()
    attempted = threading.Event()
    first_held = threading.Event()
    release = threading.Event()
    failures = []
    real_lock = index_file.locked_account_index

    @contextmanager
    def watched_lock():
        peer = threading.current_thread().name == "account-peer"
        if peer:
            attempted.set()
        with real_lock():
            if peer:
                acquired.set()
            yield

    monkeypatch.setattr(manager_module, "locked_account_index", watched_lock)
    monkeypatch.setattr(index_file, "locked_account_index", watched_lock)
    monkeypatch.setattr(naming_module, "locked_account_index", watched_lock)
    calls = 0

    def guard():
        nonlocal calls
        calls += 1
        if calls == 2:
            first_held.set()
            assert release.wait(10), "peer coordination deadline"
        return True

    service = AccountNamingService(accounts_index_path=manager.accounts_index_file,
                                  user_database_path=manager.account_dir("A") / "user_data.sqlite3",
                                  account_id="A", context_is_current=guard)

    def run(action):
        try:
            action()
        except BaseException as error:
            failures.append(error)

    actions = {"initialize": manager.initialize, "create": lambda: manager.create_account("C"),
               "delete": lambda: manager.delete_account("B"), "active": lambda: manager.set_active_account_id("B"),
               "seed": lambda: manager.seed_account_data("A"), "rename": lambda: manager.rename_account("B", "B-new"),
               "write": lambda: manager.write_index(manager.read_index())}
    first = threading.Thread(name="account-naming", target=run, args=(lambda: service.rename("A-new"),))
    peer = threading.Thread(name="account-peer", target=run, args=(actions[writer],))
    first.start()
    try:
        assert first_held.wait(3)
        peer.start()
        assert attempted.wait(3)
        assert not acquired.wait(0.05), "peer entered actual index critical section before release"
        with closing(sqlite3.connect(manager.account_dir("A") / "user_data.sqlite3")) as observer:
            assert observer.execute("SELECT account_name FROM database_profile").fetchone()[0] == "A"
    finally:
        release.set()
        first.join(3)
        if peer.ident is not None:
            peer.join(3)
    assert not first.is_alive() and not peer.is_alive()
    assert not failures, failures
    assert acquired.is_set()
    assert _name(manager, "A") == "A-new"
    assert next(row for row in manager.read_index()["accounts"] if row["id"] == "A")["name"] == "A-new"
    print(json.dumps({"scenario": "thread-" + writer, "actual_threads": [first.ident, peer.ident],
                      "attempt_before_release": True, "acquired_after_release": True,
                      "threads_terminal": True, "index": manager.read_index()}, ensure_ascii=False))


def _ui_import_switch_case(manager, export, failure, monkeypatch):
    from auto_sync_ui_fixture import application, dispose
    from PySide6.QtWidgets import QComboBox, QDialog, QFileDialog, QMessageBox, QPushButton, QWidget

    app = application()
    parent = QWidget()
    current_owner = ["A"]
    switch_calls = []
    messages = []
    temporary_id = []

    def switch(account_id):
        switch_calls.append(account_id)
        if len(switch_calls) == 1:
            temporary_id.append(account_id)
            current_owner[0] = account_id
            manager.set_active_account_id(account_id)
            return True
        if failure.endswith("error"):
            raise RuntimeError("SYNTHETIC_OWNER_STOP_FAILURE")
        return False

    if failure.startswith("ui-failure"):
        replace = index_file.os.replace
        injected = False

        def fail_publication(source, target):
            nonlocal injected
            if not injected and temporary_id and Path(target) == manager.accounts_index_file:
                if json.loads(Path(source).read_bytes()).get("active_account_id") == "A":
                    injected = True
                    raise OSError("SYNTHETIC_IMPORT_PUBLICATION_FAILURE")
            return replace(source, target)

        monkeypatch.setattr(index_file.os, "replace", fail_publication)

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(export), ""))
    for method in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, method,
                            lambda _parent, _title, message, kind=method: messages.append((kind, message)))

    def interact(dialog):
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "导入数据").click()
        app.processEvents()
        assert len(switch_calls) == 2
        assert current_owner[0] == temporary_id[0]
        assert dialog.findChild(QComboBox).currentData() == temporary_id[0]
        return 0

    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        manager_module.show_account_manager_dialog(parent, "", manager, "A", switch, lambda: None)
        temp_id = temporary_id[0]
        assert manager.account_dir(temp_id).is_dir()
        assert any(row["id"] == temp_id for row in manager.read_index()["accounts"])
        assert _name(manager, temp_id) == "导入临时账号"
        assert len(messages) == 1
        kind, text = messages[0]
        if "success" in failure:
            assert kind == "warning" and "已导入" in text and "切换尚未完成" in text, messages
            assert _name(manager, "A") == "A"
        else:
            assert kind == "critical" and "导入失败" in text and "回切尚未完成" in text
            assert _name(manager, "A") == "legacy-profile"
        print(json.dumps({"scenario": failure, "switch_calls": switch_calls,
                          "actual_runtime_owner": current_owner[0], "temporary_retained": True,
                          "messages": messages}, ensure_ascii=False))
    finally:
        dispose(parent)


@pytest.mark.parametrize("failure", ("success", "file", "target-index", "target-directory",
                                     "ui-success-false", "ui-success-error", "ui-failure-false", "ui-failure-error"))
def test_import_rechecks_after_unlocked_stop_and_merges_only_target(tmp_path, monkeypatch, failure):
    manager = _manager(tmp_path)
    database = manager.account_dir("A") / "user_data.sqlite3"
    with UserDataDao(database) as dao:
        dao.rename_account("legacy-profile")
    export = export_account_data(manager, "A", tmp_path / "synthetic-account.zip")
    if failure.startswith("ui-"):
        _ui_import_switch_case(manager, export, failure, monkeypatch)
        return
    before_database = _sha(database)
    callback_finished = threading.Event()
    peer_errors = []

    def callback(target_id):
        assert target_id == "A"

        def peer():
            try:
                _peer_change(manager)
                if failure == "target-index":
                    manager.rename_account("A", "third-party-name")
                elif failure == "target-directory":
                    manager.account_dir("A").replace(tmp_path / "third-party-previous")
                    with UserDataDao(manager.account_dir("A") / "user_data.sqlite3",
                                     account_id="A", account_name="A"):
                        pass
            except BaseException as error:
                peer_errors.append(error)
            finally:
                callback_finished.set()

        worker = threading.Thread(name="import-stop-peer", target=peer)
        worker.start()
        worker.join(3)
        assert not worker.is_alive(), "stop callback was invoked while index lock was held"
        assert not peer_errors, peer_errors

    replace = index_file.os.replace
    injected = False

    def failing_replace(source, target):
        nonlocal injected
        if failure == "file" and callback_finished.is_set() and not injected and Path(target) == manager.accounts_index_file:
            if json.loads(Path(source).read_bytes()).get("active_account_id") == "A":
                injected = True
                raise OSError("SYNTHETIC_IMPORT_REPLACE_FAILURE")
        return replace(source, target)

    monkeypatch.setattr(index_file.os, "replace", failing_replace)
    if failure == "success":
        assert import_account_data(manager, export, before_replace=callback) == "A"
        assert _name(manager, "A") == "A"
    else:
        with pytest.raises((OSError, ValueError, sqlite3.IntegrityError)) as caught:
            import_account_data(manager, export, before_replace=callback)
        print(json.dumps({"scenario": "import-" + failure, "error": str(caught.value)}))
        if failure == "file":
            assert _sha(database) == before_database
    assert callback_finished.is_set()
    current = manager.read_index()
    names = {row["id"]: row["name"] for row in current["accounts"]}
    assert names["B"] == "B-new" and names["C"] == "C" and _name(manager, "B") == "B-new"
    assert current["active_account_id"] == ("A" if failure == "success" else "B")
    if failure == "target-index":
        assert names["A"] == "third-party-name" and _name(manager, "A") == "third-party-name"
    print(json.dumps({"scenario": "import-" + failure, "stop_callback_lock_free": True,
                      "index": current, "target_profile": _name(manager, "A")}, ensure_ascii=False))


def test_import_rollback_io_failure_retains_original_recovery_directory(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    database = manager.account_dir("A") / "user_data.sqlite3"
    original = database.read_bytes()
    index = manager.accounts_index_file.read_bytes()
    archive = export_account_data(manager, "A", tmp_path / "account.zip")
    replace = index_file.os.replace

    def fail_publication_and_restore(source, target):
        if Path(target) == manager.accounts_index_file or Path(source).name == "previous-account":
            raise OSError("SYNTHETIC_STORAGE_BUSY")
        return replace(source, target)

    monkeypatch.setattr(index_file.os, "replace", fail_publication_and_restore)
    with pytest.raises(AccountImportRecoveryError):
        import_account_data(manager, archive)
    assert manager.accounts_index_file.read_bytes() == index
    originals = list(manager.accounts_dir.glob(".account-import-*/previous-account/user_data.sqlite3"))
    assert len(originals) == 1
    assert originals[0].read_bytes() == original


@pytest.mark.parametrize("failure", ("false", "error", "success", "target-index", "target-directory", "file", "nonactive"))
def test_delete_stops_actual_owner_before_guarded_publication(tmp_path, monkeypatch, failure):
    from auto_sync_ui_fixture import application, dispose
    from PySide6.QtWidgets import QComboBox, QDialog, QMessageBox, QPushButton, QWidget

    manager = _manager(tmp_path)
    # 有意让索引 active 与运行时 owner 不同，确认删除不借索引字段推断实际 owner。
    manager.set_active_account_id("B")
    current_owner = ["B" if failure == "nonactive" else "A"]
    original = manager.accounts_index_file.read_bytes()
    callback_calls = []
    warnings = []
    callback_errors = []
    app = application()
    parent = QWidget()

    def perform_switch(account_id):
        callback_calls.append(account_id)
        assert manager.account_dir("A").is_dir()
        assert any(row["id"] == "A" for row in manager.read_index()["accounts"])
        if failure == "error":
            raise RuntimeError("SYNTHETIC_DELETE_OWNER_STOP_FAILURE")
        if failure == "false":
            return False
        current_owner[0] = account_id
        manager.set_active_account_id(account_id)
        if failure == "target-index":
            manager.rename_account("A", "A-peer")
        elif failure == "target-directory":
            manager.account_dir("A").replace(tmp_path / "peer-previous")
            with UserDataDao(manager.account_dir("A") / "user_data.sqlite3", account_id="A", account_name="A"):
                pass
        return True

    def switch(account_id):
        try:
            return perform_switch(account_id)
        except Exception as error:
            callback_errors.append(repr(error))
            raise

    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, message: warnings.append(message))
    replace = index_file.os.replace

    def failing_replace(source, target):
        if failure == "file" and Path(target) == manager.accounts_index_file:
            if not any(row["id"] == "A" for row in json.loads(Path(source).read_bytes())["accounts"]):
                raise OSError("SYNTHETIC_DELETE_REPLACE_FAILURE")
        return replace(source, target)

    monkeypatch.setattr(index_file.os, "replace", failing_replace)

    def interact(dialog):
        combo = dialog.findChild(QComboBox)
        combo.setCurrentIndex(combo.findData("A"))
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "删除").click()
        app.processEvents()
        if failure in {"target-index", "target-directory", "file", "success", "nonactive"}:
            assert combo.currentData() == current_owner[0], {"warnings": warnings, "callback_errors": callback_errors}
        return 0

    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        manager_module.show_account_manager_dialog(parent, "", manager, current_owner[0], switch, lambda: None)
        current = manager.read_index()
        ids = {row["id"] for row in current["accounts"]}
        if failure in {"false", "error"}:
            assert current_owner[0] == "A"
            assert manager.accounts_index_file.read_bytes() == original
            assert "A" in ids and manager.account_dir("A").is_dir()
            assert len(warnings) == 1 and "删除未开始" in warnings[0]
        elif failure in {"success", "nonactive"}:
            assert "A" not in ids and not manager.account_dir("A").exists()
            assert not warnings
            assert current_owner[0] == ("B" if failure == "nonactive" else "default")
        else:
            assert current_owner[0] == "default" and "A" in ids and manager.account_dir("A").is_dir()
            assert len(warnings) == 1 and "删除尚未完成" in warnings[0]
            if failure == "target-index":
                assert _name(manager, "A") == "A-peer"
        assert (not callback_calls) if failure == "nonactive" else len(callback_calls) == 1
        print(json.dumps({"scenario": "delete-" + failure, "switch_calls": callback_calls,
                          "actual_runtime_owner": current_owner[0], "index": current, "warnings": warnings}, ensure_ascii=False))
    finally:
        dispose(parent)
