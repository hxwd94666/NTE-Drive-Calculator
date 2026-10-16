# 验证旧导入遗留的 default 分叉身份修复仅在确认及一致性备份后执行，不改业务数据。
import json
from pathlib import Path
import shutil
import sqlite3
from unittest.mock import Mock

import pytest

from src.integrations.distribution.lock import DataRootLease
from src.services.runtime_data_upgrade import repair_legacy_account_identities
from src.storage.sqlite.runtime_data_snapshot import DataSnapshotError, database_identity
from src.storage.sqlite.user_data_dao import UserDataDao
from src.ui.application_runtime import prepare_application_runtime


def legacy_root(root, *, conflict="default", name="旧导入", key="default_2"):
    path = root / "accounts" / key / "user_data.sqlite3"
    with UserDataDao(path, account_id=conflict, account_name=name) as dao:
        dao.mark_legacy_application_settings_imported()
        dao.replace_application_setting_copy("ui", {"retained": "fixture"})
    index = root / "accounts/accounts.json"
    index.write_text(json.dumps({"active_account_id": key, "accounts": [{"id": key, "name": "旧导入"}]}), encoding="utf8")
    return path, index


def test_legacy_import_startup_requires_confirmation_and_retains_business_rows(tmp_path, monkeypatch):
    from src.ui import startup_data_selection
    root = tmp_path / "data"
    path, index = legacy_root(root)
    before_index = index.read_bytes()
    with sqlite3.connect(path) as db:
        before = list(db.iterdump())
    confirm = Mock(return_value=True)
    monkeypatch.setattr(startup_data_selection, "confirm_legacy_account_identity_repair", confirm)
    locale = tmp_path / "assets/locales/zh-CN.json"
    locale.parent.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[2] / "assets/locales/zh-CN.json", locale)
    # 只验证正式启动的身份/升级入口，不创建主窗口或接入游戏。
    with prepare_application_runtime(package_root=tmp_path, app_dir=tmp_path, data_root=root):
        pass
    assert database_identity(path, "default_2")["account_id"] == "default_2"
    assert json.loads(index.read_bytes()) == json.loads(before_index)
    with sqlite3.connect(path) as db:
        after = list(db.iterdump())
    assert [line for line in before if 'INSERT INTO "database_profile"' not in line] == [
        line for line in after if 'INSERT INTO "database_profile"' not in line]
    confirm.assert_called_once()
    records = list((root / "maintenance/account-identity-recovery").glob("*/record.json"))
    assert len(records) == 1 and json.loads(records[0].read_text("utf8"))["state"] == "completed"
    original = records[0].parent / "accounts/default_2/user_data.sqlite3"
    assert database_identity(original, "default")["account_id"] == "default"
    again = Mock(side_effect=AssertionError("repaired identities must not prompt again"))
    with DataRootLease(root) as lease:
        assert repair_legacy_account_identities(root, lease, confirm=again) is None
    again.assert_not_called()


def test_declining_repair_preserves_original_and_releases_startup_lease(tmp_path, monkeypatch):
    from src.ui import startup_data_selection
    path, index = legacy_root(tmp_path)
    before = path.read_bytes(), index.read_bytes()
    monkeypatch.setattr(startup_data_selection, "confirm_legacy_account_identity_repair", Mock(return_value=False))
    with pytest.raises(RuntimeError, match="已取消"):
        prepare_application_runtime(package_root=tmp_path, app_dir=tmp_path, data_root=tmp_path)
    assert (path.read_bytes(), index.read_bytes()) == before
    assert not (tmp_path / "maintenance/account-identity-recovery").exists()
    with DataRootLease(tmp_path):
        pass


@pytest.mark.parametrize("kind", ["other_identity", "different_name", "not_legacy_suffix", "future_schema"])
def test_unknown_conflicts_are_not_rebound_or_prompted(tmp_path, kind):
    path, index = legacy_root(tmp_path, conflict="another" if kind == "other_identity" else "default",
                              name="不匹配" if kind == "different_name" else "旧导入",
                              key="account_2" if kind == "not_legacy_suffix" else "default_2")
    if kind == "future_schema":
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO schema_migration VALUES (999, 'fixture')")
    before = path.read_bytes(), index.read_bytes()
    confirm = Mock(side_effect=AssertionError("unknown identity must stop"))
    with DataRootLease(tmp_path) as lease, pytest.raises(DataSnapshotError):
        repair_legacy_account_identities(tmp_path, lease, confirm=confirm)
    confirm.assert_not_called()
    assert (path.read_bytes(), index.read_bytes()) == before


def test_partial_repair_failure_rolls_back_completed_identities(tmp_path, monkeypatch):
    from src.services import runtime_data_upgrade as upgrade
    path, index = legacy_root(tmp_path)
    second = tmp_path / "accounts/default_3/user_data.sqlite3"
    with UserDataDao(second, account_id="default", account_name="旧导入"):
        pass
    data = json.loads(index.read_text("utf8"))
    data["accounts"].append({"id": "default_3", "name": "旧导入"})
    index.write_text(json.dumps(data), encoding="utf8")
    original = upgrade.repair_legacy_import_identity
    def fail_second(database, key, name, *, rollback=False):
        if key == "default_3" and not rollback:
            raise OSError("fixture repair write failure")
        return original(database, key, name, rollback=rollback)
    monkeypatch.setattr(upgrade, "repair_legacy_import_identity", fail_second)
    with DataRootLease(tmp_path) as lease, pytest.raises(OSError, match="fixture repair"):
        repair_legacy_account_identities(tmp_path, lease, confirm=lambda *_: True)
    assert database_identity(path, "default")["account_id"] == "default"
    assert database_identity(second, "default")["account_id"] == "default"
    records = list((tmp_path / "maintenance/account-identity-recovery").glob("*/record.json"))
    assert json.loads(records[0].read_text("utf8"))["state"] == "failed_rolled_back"


def test_backup_failure_does_not_modify_original_identity(tmp_path, monkeypatch):
    from src.services import runtime_data_upgrade as upgrade
    path, index = legacy_root(tmp_path)
    before = path.read_bytes(), index.read_bytes()
    monkeypatch.setattr(upgrade, "backup_database", Mock(side_effect=OSError("fixture backup failure")))
    with DataRootLease(tmp_path) as lease, pytest.raises(OSError, match="fixture backup"):
        repair_legacy_account_identities(tmp_path, lease, confirm=lambda *_: True)
    assert (path.read_bytes(), index.read_bytes()) == before


def test_startup_repair_confirmation_defaults_to_cancel(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from src.ui.startup_data_selection import confirm_legacy_account_identity_repair
    app = QApplication.instance() or QApplication([])
    warning = Mock(return_value=QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(QMessageBox, "warning", warning)
    monkeypatch.setattr(QApplication, "beep", Mock())
    assert not confirm_legacy_account_identity_repair(tmp_path, 2)
    assert warning.call_args.args[-1] == QMessageBox.StandardButton.Cancel
    assert app is QApplication.instance()
