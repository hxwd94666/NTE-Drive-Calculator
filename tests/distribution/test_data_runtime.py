# 验证显式数据根、进程独占、旧根迁入和升级恢复，不操作真实账号或组件。
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import Mock

import pytest

from src.integrations.application_runtime_paths import select_runtime_data_root
from src.integrations.distribution.data_roots import select_external_data_root, resolve_external_data_root
from src.integrations.distribution.data_lifecycle import (
    prepare_data_upgrade, finish_data_upgrade, migrate_existing_data, restore_data_recovery, resume_data_import,
)
from src.integrations.distribution.lock import DataRootLease
from src.integrations.distribution.metadata import Distribution, DistributionError, load_distribution, parse_distribution
from src.services.runtime_data_upgrade import apply_runtime_data_upgrades
from src.storage.sqlite.user_data_dao import UserDataDao
from src.storage.sqlite.runtime_data_snapshot import database_identity

ROOT = Path(__file__).resolve().parents[2]


def portable(**kwargs):
    return Distribution(distribution="portable", update_channel="manual", version="2.3.1", build_id="fixture", **kwargs)


def account_root(root, ids=("one",), *, old=False, imported=True):
    for key in ids:
        path = root / "accounts" / key / "user_data.sqlite3"
        with UserDataDao(path, account_id=key) as dao:
            if imported:
                dao.mark_legacy_application_settings_imported()
        if old:
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("DROP TABLE cultivation_history")
                db.execute("DELETE FROM schema_migration WHERE version>=45")
    (root / "accounts/accounts.json").write_text(json.dumps({"active_account_id": ids[0],
        "accounts": [{"id": key, "name": key} for key in ids]}), encoding="utf-8")
    (root / "config").mkdir(exist_ok=True)
    (root / "config/global_ui_preferences.json").write_text('{"theme":"light","locale":"zh-CN"}')


def test_metadata_and_missing_portable_identity_do_not_fall_back(tmp_path):
    value = asdict(portable())
    assert parse_distribution(value).portable
    value["data_directory"] = "../elsewhere"
    with pytest.raises(DistributionError):
        parse_distribution(value)
    assert load_distribution(tmp_path).distribution == "installed"
    marker = tmp_path / "userdata/maintenance/accepted-release.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}")
    with pytest.raises(DistributionError, match="标识丢失"):
        load_distribution(tmp_path)


def test_portable_root_stable_across_cwd_and_permission_failure(tmp_path, monkeypatch):
    app = tmp_path / "中文 程序"
    expected = app / "userdata"
    monkeypatch.chdir(tmp_path)
    assert select_runtime_data_root(app, distribution=portable()) == expected
    original = Path.mkdir
    def denied(path, *args, **kwargs):
        if path == expected:
            raise PermissionError("fixture")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", denied)
    with pytest.raises(DistributionError, match="未切换"):
        select_runtime_data_root(app, distribution=portable(), local_appdata=str(tmp_path / "fallback"))
    assert not (tmp_path / "fallback").exists()


def test_external_root_requires_persisted_matching_identity(tmp_path):
    metadata = Distribution(distribution="portable", update_channel="platform", data_policy="external",
                            data_root_key="platform-slot", version="2.3.1", build_id="fixture")
    home, root = tmp_path / "local", tmp_path / "保存数据"
    with pytest.raises(DistributionError):
        resolve_external_data_root(metadata, selection_home=home)
    assert select_external_data_root(metadata, root, selection_home=home) == root
    assert resolve_external_data_root(metadata, selection_home=home) == root
    with pytest.raises(DistributionError, match="改绑"):
        select_external_data_root(metadata, tmp_path / "empty", selection_home=home)
    (root / "maintenance/data-root.json").unlink()
    with pytest.raises(DistributionError):
        resolve_external_data_root(metadata, selection_home=home)
    assert not (tmp_path / "empty").exists()


def test_data_lease_blocks_a_real_second_process_and_releases(tmp_path):
    code = ("from pathlib import Path; from src.integrations.distribution.lock import DataRootLease; "
            "lease=DataRootLease(Path(__import__('sys').argv[1])); lease.close()")
    with DataRootLease(tmp_path):
        blocked = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=ROOT, capture_output=True)
        assert blocked.returncode != 0
        assert b"DistributionError" in blocked.stderr
    released = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=ROOT, capture_output=True)
    assert released.returncode == 0, released.stderr


def test_in_place_legacy_migration_preserves_original_and_does_not_copy_loader(tmp_path):
    account_root(tmp_path)
    (tmp_path / "config/native-loader").mkdir()
    (tmp_path / "config/native-loader/payload.dll").write_bytes(b"not-a-runtime-copy")
    source = tmp_path / "accounts/one/user_data.sqlite3"
    before = source.read_bytes()
    target = tmp_path / "userdata"
    with DataRootLease(target) as lease:
        with pytest.raises(DistributionError, match="确认"):
            migrate_existing_data(tmp_path, target, lease, confirm_source_closed=False)
        migrate_existing_data(tmp_path, target, lease, confirm_source_closed=True)
        with pytest.raises(DistributionError, match="已有"):
            migrate_existing_data(tmp_path, target, lease, confirm_source_closed=True)
    assert source.read_bytes() == before
    assert database_identity(target / "accounts/one/user_data.sqlite3", "one")["schema"] == 45
    assert not (target / "config/native-loader").exists()
    assert json.loads((target / "maintenance/last-import.json").read_text())["machine_paths_require_revalidation"]


def test_import_interruption_resumes_without_rereading_source(tmp_path, monkeypatch):
    source, target = tmp_path / "old", tmp_path / "new"
    account_root(source)
    original = os.replace
    def fail(source_path, target_path):
        if Path(target_path) == target / "config":
            raise OSError("publish interruption")
        return original(source_path, target_path)
    with DataRootLease(target) as lease:
        monkeypatch.setattr(os, "replace", fail)
        with pytest.raises(OSError):
            migrate_existing_data(source, target, lease, confirm_source_closed=True)
        assert (target / "maintenance/pending-import.json").exists()
        monkeypatch.setattr(os, "replace", original)
        resume_data_import(target, lease)
    assert (target / "accounts/accounts.json").is_file()
    assert not (target / "maintenance/pending-import.json").exists()


def test_upgrade_all_accounts_once_and_restore_preserves_newer_data(tmp_path):
    account_root(tmp_path, ("one", "two"), old=True)
    with DataRootLease(tmp_path) as lease:
        snapshot = prepare_data_upgrade(tmp_path, ROOT, portable(), lease)
        assert snapshot is not None
        record = json.loads((snapshot / "record.json").read_text())
        assert {row["account_id"] for row in record["databases"]} == {"one", "two"}
        apply_runtime_data_upgrades(tmp_path, ROOT, snapshot, lease)
        finish_data_upgrade(tmp_path, snapshot, lease)
        assert prepare_data_upgrade(tmp_path, ROOT, portable(), lease) is None
        with UserDataDao(tmp_path / "accounts/one/user_data.sqlite3") as dao:
            dao.rename_account("newer-name")
        new_config = tmp_path / "config/new-preference.json"
        new_config.write_text('{"new":true}')
        kept = restore_data_recovery(tmp_path, snapshot.name, portable(), lease, confirm_replace=True)
        assert (kept / "files/config/new-preference.json").is_file()
        assert not new_config.exists()
        for key in ("one", "two"):
            assert database_identity(tmp_path / f"accounts/{key}/user_data.sqlite3", key)["schema"] == 44
        with closing(sqlite3.connect(kept / "files/accounts/one/user_data.sqlite3")) as db:
            assert db.execute("SELECT account_name FROM database_profile").fetchone()[0] == "newer-name"


def test_backup_failure_prevents_migration_and_pending_failure_requires_restore(tmp_path, monkeypatch):
    from src.integrations.distribution import data_lifecycle
    account_root(tmp_path, old=True)
    with DataRootLease(tmp_path) as lease:
        with monkeypatch.context() as patch:
            patch.setattr(data_lifecycle, "copy_snapshot_file", Mock(side_effect=OSError("disk full")))
            with pytest.raises(OSError):
                prepare_data_upgrade(tmp_path, ROOT, portable(), lease)
        assert database_identity(tmp_path / "accounts/one/user_data.sqlite3", "one")["schema"] == 44
        snapshot = prepare_data_upgrade(tmp_path, ROOT, portable(), lease)
        assert snapshot
        with pytest.raises(DistributionError, match="上次"):
            prepare_data_upgrade(tmp_path, ROOT, portable(), lease)


def test_normal_startup_only_inspects_schema_not_full_integrity(tmp_path, monkeypatch):
    from src.integrations.distribution import data_lifecycle
    account_root(tmp_path)
    calls = Mock(side_effect=AssertionError("no backup for unchanged data"))
    monkeypatch.setattr(data_lifecycle, "_make_snapshot", calls)
    with DataRootLease(tmp_path) as lease:
        assert prepare_data_upgrade(tmp_path, ROOT, portable(), lease) is None
    calls.assert_not_called()


def test_current_theme_survives_other_account_legacy_upgrade(tmp_path):
    account_root(tmp_path, ("one", "two"), imported=False)
    for key, theme in (("one", "light"), ("two", "dark")):
        with UserDataDao(tmp_path / f"accounts/{key}/user_data.sqlite3") as dao:
            dao.replace_application_setting_copy("ui", {"theme": theme})
    (tmp_path / "config/global_ui_preferences.json").unlink()
    with DataRootLease(tmp_path) as lease:
        snapshot = prepare_data_upgrade(tmp_path, ROOT, portable(), lease)
        apply_runtime_data_upgrades(tmp_path, ROOT, snapshot, lease)
        finish_data_upgrade(tmp_path, snapshot, lease)
        assert prepare_data_upgrade(tmp_path, ROOT, portable(), lease) is None
    assert json.loads((tmp_path / "config/global_ui_preferences.json").read_text())["theme"] == "light"


def test_interrupted_restore_replays_without_snapshotting_mixed_schema(tmp_path, monkeypatch):
    account_root(tmp_path, ("one", "two"), old=True)
    with DataRootLease(tmp_path) as lease:
        snapshot = prepare_data_upgrade(tmp_path, ROOT, portable(), lease)
        apply_runtime_data_upgrades(tmp_path, ROOT, snapshot, lease)
        finish_data_upgrade(tmp_path, snapshot, lease)
        original = os.replace
        def fail(source, destination):
            if Path(destination) == tmp_path / "accounts/two/user_data.sqlite3":
                raise OSError("restore interrupted")
            return original(source, destination)
        monkeypatch.setattr(os, "replace", fail)
        with pytest.raises(OSError):
            restore_data_recovery(tmp_path, snapshot.name, portable(), lease, confirm_replace=True)
        monkeypatch.setattr(os, "replace", original)
        kept = restore_data_recovery(tmp_path, snapshot.name, portable(), lease, confirm_replace=True)
        assert kept.is_dir()
        assert len(list((tmp_path / "maintenance/data-recovery").iterdir())) == 2
        assert not (tmp_path / "maintenance/pending-upgrade.json").exists()
        for key in ("one", "two"):
            assert database_identity(tmp_path / f"accounts/{key}/user_data.sqlite3", key)["schema"] == 44


def test_explicit_cli_migration_shows_machine_revalidation(tmp_path, monkeypatch, capsys):
    import main
    from src.integrations.distribution import maintenance_cli
    from src.ui import app
    source, install = tmp_path / "old", tmp_path / "portable"
    account_root(source)
    install.mkdir()
    monkeypatch.setattr(maintenance_cli, "resolve_application_roots", lambda: (install, install))
    monkeypatch.setattr(maintenance_cli, "load_distribution", lambda _path: portable())
    monkeypatch.setattr(maintenance_cli, "verify_release", lambda *_args: None)
    gui = Mock(side_effect=AssertionError("maintenance must exit before GUI"))
    monkeypatch.setattr(app, "run_gui", gui)
    monkeypatch.setattr(sys, "argv", ["NTE_Drive_Calc.exe", "--migrate-data-from", str(source), "--confirm-source-closed"])
    main.main()
    gui.assert_not_called()
    assert "环境检测重新确认" in capsys.readouterr().out
    assert (install / "userdata/accounts/accounts.json").is_file()


def test_templates_seed_missing_only(tmp_path):
    from src.features.accounts.manager import AccountManager
    bundled, root = tmp_path / "bundle", tmp_path / "data"
    (bundled / "templates").mkdir(parents=True)
    (bundled / "templates/one.json").write_text("new-bundle")
    (bundled / "templates/two.json").write_text("new-default")
    (root / "config/templates").mkdir(parents=True)
    (root / "config/templates/one.json").write_text("user-edited")
    AccountManager(root, bundled, lambda *_: [], (), ()).seed_user_config()
    assert (root / "config/templates/one.json").read_text() == "user-edited"
    assert (root / "config/templates/two.json").read_text() == "new-default"


def test_high_schema_and_bad_index_stop_before_startup_writes(tmp_path):
    account_root(tmp_path)
    database = tmp_path / "accounts/one/user_data.sqlite3"
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("INSERT INTO schema_migration VALUES (999,'future')")
    with DataRootLease(tmp_path) as lease:
        with pytest.raises(RuntimeError, match="高于"):
            prepare_data_upgrade(tmp_path, ROOT, portable(), lease)
        assert not (tmp_path / "maintenance").exists()
        (tmp_path / "accounts/accounts.json").write_text("broken")
        with pytest.raises(DistributionError, match="索引损坏"):
            prepare_data_upgrade(tmp_path, ROOT, portable(), lease)


def test_legacy_theme_file_is_preserved_when_database_needs_no_migration(tmp_path):
    account_root(tmp_path)
    (tmp_path / "config/global_ui_preferences.json").unlink()
    config = tmp_path / "accounts/one/config"
    config.mkdir()
    (config / "ui_preferences.json").write_text('{"theme":"light"}')
    with DataRootLease(tmp_path) as lease:
        snapshot = prepare_data_upgrade(tmp_path, ROOT, portable(), lease)
        apply_runtime_data_upgrades(tmp_path, ROOT, snapshot, lease)
        finish_data_upgrade(tmp_path, snapshot, lease)
        assert prepare_data_upgrade(tmp_path, ROOT, portable(), lease) is None
    assert json.loads((tmp_path / "config/global_ui_preferences.json").read_text())["theme"] == "light"


def test_partial_import_staging_blocks_default_account_and_allows_explicit_retry(tmp_path, monkeypatch):
    from src.integrations.distribution import data_lifecycle
    from src.ui.application_runtime import prepare_application_runtime
    source, target = tmp_path / "old", tmp_path / "new"
    account_root(source)
    original = data_lifecycle.copy_snapshot_file
    with DataRootLease(target) as lease:
        monkeypatch.setattr(data_lifecycle, "copy_snapshot_file", Mock(side_effect=OSError("copy stopped")))
        with pytest.raises(OSError):
            migrate_existing_data(source, target, lease, confirm_source_closed=True)
        assert json.loads((target / "maintenance/pending-import.json").read_text())["state"] == "preparing"
        with pytest.raises(DistributionError, match="尚未完整"):
            resume_data_import(target, lease)
    with pytest.raises(DistributionError, match="迁入尚未完成"):
        prepare_application_runtime(package_root=tmp_path, app_dir=tmp_path, data_root=target)
    assert not (target / "accounts").exists()
    monkeypatch.setattr(data_lifecycle, "copy_snapshot_file", original)
    with DataRootLease(target) as lease:
        migrate_existing_data(source, target, lease, confirm_source_closed=True)
    assert (target / "accounts/accounts.json").is_file()


def test_failed_data_confirmation_restores_previously_accepted_release(tmp_path, monkeypatch):
    from src.integrations.distribution import startup_commit
    maintenance = tmp_path / "maintenance"
    maintenance.mkdir()
    accepted = maintenance / "accepted-release.json"
    previous = b'{"old":"accepted-exact-bytes"}\r\n'
    accepted.write_bytes(previous)
    (maintenance / "pending-upgrade.json").write_text('{"recovery_id":"pending"}')
    monkeypatch.setattr(startup_commit, "accept_release", lambda *_: accepted.write_text('{"new":"unaccepted"}'))
    monkeypatch.setattr(startup_commit, "finish_data_upgrade", Mock(side_effect=OSError("record disk full")))
    with DataRootLease(tmp_path) as lease:
        with pytest.raises(OSError):
            startup_commit.commit_runtime_acceptance(tmp_path, object(), tmp_path / "snapshot", lease)
    assert accepted.read_bytes() == previous
    assert (maintenance / "pending-upgrade.json").exists()
    assert not (maintenance / "pending-acceptance.json").exists()


def test_failed_acceptance_rollback_is_recovered_before_next_start(tmp_path, monkeypatch):
    from src.integrations.distribution import startup_commit
    from src.integrations.distribution.data_roots import write_record
    import hashlib
    maintenance = tmp_path / "maintenance"
    maintenance.mkdir()
    previous = b'{"old":"accepted"}'
    (maintenance / "previous-accepted-release.json").write_bytes(previous)
    (maintenance / "accepted-release.json").write_text('{"new":"not-committed"}')
    write_record(maintenance / "pending-acceptance.json", {"schema_version": 1,
                 "previous_sha256": hashlib.sha256(previous).hexdigest()})
    with DataRootLease(tmp_path) as lease:
        startup_commit.recover_interrupted_acceptance(tmp_path, lease)
    assert (maintenance / "accepted-release.json").read_bytes() == previous


def test_first_portable_identity_prevents_later_missing_metadata_fallback(tmp_path):
    from src.integrations.distribution.data_roots import establish_portable_identity
    root = tmp_path / "userdata"
    with DataRootLease(root) as lease:
        establish_portable_identity(root, portable(), lease)
    with pytest.raises(DistributionError, match="标识丢失"):
        load_distribution(tmp_path)


def test_external_selector_is_serialized_by_locator_not_chosen_root(tmp_path):
    from src.integrations.distribution.data_roots import _locator
    metadata = portable(data_policy="external", data_root_key="fixture")
    home = tmp_path / "local"
    code = ("from pathlib import Path; from src.integrations.distribution.metadata import Distribution; "
            "from src.integrations.distribution.data_roots import select_external_data_root; import sys; "
            "select_external_data_root(Distribution(data_policy='external',data_root_key='fixture'), "
            "Path(sys.argv[1]),selection_home=Path(sys.argv[2]))")
    with DataRootLease(_locator(metadata, home).parent):
        result = subprocess.run([sys.executable, "-c", code, str(tmp_path / "other"), str(home)],
                                cwd=ROOT, capture_output=True)
        assert result.returncode != 0 and b"DistributionError" in result.stderr
        assert not (tmp_path / "other").exists()
    assert select_external_data_root(metadata, tmp_path / "chosen", selection_home=home) == tmp_path / "chosen"


def test_acceptance_final_marker_failure_reinstates_pending_upgrade(tmp_path, monkeypatch):
    from src.integrations.distribution import startup_commit
    from src.integrations.distribution.data_roots import write_record
    maintenance = tmp_path / "maintenance"
    pending = maintenance / "pending-upgrade.json"
    write_record(pending, {"recovery_id": "fixture"})
    monkeypatch.setattr(startup_commit, "accept_release", lambda *_: None)
    monkeypatch.setattr(startup_commit, "finish_data_upgrade", lambda *_: pending.unlink())
    original = Path.unlink
    failed = []
    def fail_once(path, *args, **kwargs):
        if path.name == "pending-acceptance.json" and not failed:
            failed.append(True)
            raise OSError("final marker delete failed")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_once)
    with DataRootLease(tmp_path) as lease:
        with pytest.raises(OSError):
            startup_commit.commit_runtime_acceptance(tmp_path, object(), tmp_path / "snapshot", lease)
    assert json.loads(pending.read_text())["recovery_id"] == "fixture"
