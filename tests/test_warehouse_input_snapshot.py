# 验证仓库输入与并发写入隔离，保留来源和锁，不读取整库历史或留下取消残片。
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from concurrent.futures import CancelledError
from src.storage.sqlite.user_data_dao import UserDataDao
from src.storage.sqlite.warehouse_input_snapshot import freeze_warehouse_inputs
from src.storage.sqlite.warehouse_recommendation_inputs import recommendation_account_identity
from src.domain.equipment.management_settings import MatchingScope
from src.domain.equipment.warehouse_decisions import DecisionStatus, WarehouseDecision
from src.services.inventory.warehouse_predecessors import PredecessorBatch
from tests.test_user_data_inventory_dao import item, snapshot


def account(tmp_path):
    path = tmp_path / "source.sqlite3"
    with UserDataDao(path, account_id="warehouse-test") as dao:
        first = dao.import_inventory_snapshot(snapshot(1, [item(11, 21)]))
        unused = dao.import_inventory_snapshot(snapshot(2, [item(12, 22)]))
        current = dao.import_inventory_snapshot(snapshot(3, [item(13, 23)]))
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE unrelated_history(payload BLOB)")
        db.execute("INSERT INTO unrelated_history VALUES(zeroblob(1048576))")
        db.execute("INSERT INTO application_setting_copy(setting_key,value_json,updated_at_utc) "
                   "VALUES('ui','{}','before')")
    return path, first, unused, current


def test_only_required_inventory_and_saved_plan_sources_are_materialized(tmp_path):
    source, first, unused, current = account(tmp_path)
    with UserDataDao(source) as dao:
        slot = dao.create_loadout_slot(1003, "locked")
        plan = dao.save_plan_to_slot(slot, name="locked", source_snapshot_id=first,
            assignments=[{"uid_serial": 11, "uid_slot": 21, "kind": "module"}],
            payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "role"})
        dao.set_allocation_lock(plan, True)
        owners = dao.list_allocation_locked_equipment_owners()
        expected = dao.export_inventory_snapshot(current)
    target = tmp_path / "inputs.sqlite3"
    assert freeze_warehouse_inputs(source, target) == current
    with UserDataDao(target) as dao:
        assert dao.profile()["account_id"] == "warehouse-test"
        assert dao.export_inventory_snapshot(current) == expected
        assert dao.current_inventory_snapshot_id() == current
        assert dao.list_allocation_locked_equipment_owners() == owners
        assert dao.inventory_snapshot_summary(first) is not None
        assert dao.inventory_snapshot_summary(unused) is None
    assert recommendation_account_identity(target) == recommendation_account_identity(source)
    with closing(sqlite3.connect(target)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        assert "unrelated_history" not in tables
        assert "battle_hit_evidence" not in tables
        assert "cultivation_history" not in tables
        assert "all_item_snapshot" not in tables
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_writes_continue_without_restarting_or_mixing_frozen_reads(tmp_path, monkeypatch):
    source, _, _, current = account(tmp_path)
    connect = sqlite3.connect
    writes = []

    class SourceConnection(sqlite3.Connection):
        def backup(self, *args, **kwargs):
            raise AssertionError("仓库不得整库备份")

        def execute(self, sql, *args, **kwargs):
            if sql.startswith('SELECT * FROM "'):
                # 每读取一张输入表，模拟同步线程提交新的账号写入；读事务应固定旧版本。
                with closing(connect(source)) as writer, writer:
                    writer.execute("UPDATE application_setting_copy SET value_json=?,updated_at_utc='after' "
                                   "WHERE setting_key='ui'", ('{"counter":' + str(len(writes)) + '}',))
                    writer.execute("INSERT INTO unrelated_history VALUES(?)", (b"sync",))
                writes.append(sql)
            return super().execute(sql, *args, **kwargs)

    def connection(database, *args, **kwargs):
        if str(database).startswith(source.as_uri()):
            kwargs["factory"] = SourceConnection
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connection)
    target = tmp_path / "inputs.sqlite3"
    assert freeze_warehouse_inputs(source, target) == current
    assert len(writes) > 1
    with closing(connect(target)) as frozen, closing(connect(source)) as live:
        assert frozen.execute("SELECT value_json FROM application_setting_copy WHERE setting_key='ui'").fetchone()[0] == "{}"
        assert live.execute("SELECT value_json FROM application_setting_copy WHERE setting_key='ui'").fetchone()[0] != "{}"
    assert len(writes) == len(set(writes)), "每张输入表只读一次，不因其他连接写入重头读取"


def test_explicit_page_snapshot_remains_selected_when_current_has_changed(tmp_path):
    source, first, _, current = account(tmp_path)
    target = tmp_path / "inputs.sqlite3"
    assert freeze_warehouse_inputs(source, target, snapshot_id=first) == first
    with UserDataDao(source) as live, UserDataDao(target) as frozen:
        assert live.current_inventory_snapshot_id() == current
        assert frozen.current_inventory_snapshot_id() == first
        assert frozen.list_inventory_items(first) == live.list_inventory_items(first)


def test_cancel_discards_partial_input_and_preserves_source(tmp_path):
    source, _, _, current = account(tmp_path)
    target = tmp_path / "inputs.sqlite3"
    checks = 0
    def cancelled():
        nonlocal checks
        checks += 1
        return checks >= 12
    with pytest.raises(CancelledError):
        freeze_warehouse_inputs(source, target, cancel_check=cancelled)
    assert not target.exists()
    with UserDataDao(source) as dao:
        assert dao.current_inventory_snapshot_id() == current


def test_missing_snapshot_and_existing_target_do_not_overwrite_data(tmp_path):
    source, _, _, _ = account(tmp_path)
    target = tmp_path / "inputs.sqlite3"
    with pytest.raises(ValueError, match="完整库存"):
        freeze_warehouse_inputs(source, target, snapshot_id=999999)
    assert not target.exists()
    target.write_bytes(b"preserve")
    with pytest.raises(FileExistsError):
        freeze_warehouse_inputs(source, target)
    assert target.read_bytes() == b"preserve"


@pytest.mark.parametrize("entry", ["quality", "first_rules", "preview"])
def test_all_three_entries_compute_from_narrow_inputs(tmp_path, monkeypatch, entry):
    from src.services.inventory import warehouse_auto_quality as auto
    from src.services.inventory import warehouse_quality_assessment as initial
    from src.services.inventory import warehouse_recommendation_preview as preview
    from src.services.identification.matching_settings import MatchingSettingsService

    source, _, _, current = account(tmp_path)
    with closing(sqlite3.connect(source)) as db, db:
        db.execute("UPDATE application_setting_copy SET value_json=? WHERE setting_key='ui'",
                   ('{"full_scan_post_action_config":{"recommendation":{"rule_scheme_version":3}}}',))
    static = tmp_path / "game_static.sqlite3"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for path in (static, static.with_name("manifest.json"), config_dir / "stats.json"):
        path.write_text("{}", encoding="utf-8")
    config = preview.post_config(source)
    matching = MatchingSettingsService(source, scope=MatchingScope.WAREHOUSE).load()
    seen = []

    def evaluate(path, *args, **kwargs):
        with UserDataDao(path) as dao, closing(sqlite3.connect(path)) as db:
            assert dao.current_inventory_snapshot_id() == current
            assert len(dao.list_inventory_items(current)) == 1
            assert db.execute("SELECT 1 FROM sqlite_schema WHERE name='battle_record'").fetchone() is None
        seen.append(path)
        return {"score": 10, "snapshot_id": current, "recommended_drive_grade": "SS",
                "drive_rate": 1, "drive_count": 1, "elapsed_seconds": 0}

    def predecessors(path, *_args, **_kwargs):
        # 本例验证窄输入经过预览的新前序入口；决定链本身由管理流水线专项验证。
        # 仍保留真实冻结库存的完整 UID 集合，不用空结果绕过预览完整性门禁。
        evaluate(path)
        with UserDataDao(path) as dao:
            rows = dao.list_inventory_items_with_runtime_state(current)
        raw = {f"nte-{row['kind']}-{row['uid_slot']}-{row['uid_serial']}": row for row in rows}
        decisions = {uid: WarehouseDecision(uid, DecisionStatus.UNDECIDED, "manual") for uid in raw}
        return PredecessorBatch(current, tuple(SimpleNamespace(uid=uid) for uid in raw), raw, decisions)

    monkeypatch.setattr(auto, "assess_quality", evaluate)
    monkeypatch.setattr(initial, "assess_quality", evaluate)
    monkeypatch.setattr(auto, "configured_workshop_weight_template_file", lambda: None)
    monkeypatch.setattr(initial, "configured_workshop_weight_template_file", lambda: None)
    monkeypatch.setattr(initial, "generate_warehouse_rules", lambda *args, **kwargs: {"rules": [], "options": {}})
    monkeypatch.setattr(preview, "run_warehouse_trial", lambda **kwargs: evaluate(kwargs["account_snapshot"]))
    monkeypatch.setattr(preview, "evaluate_predecessors", predecessors)
    args = dict(database=source, static_database=static, config_dir=config_dir, config=config,
                matching=matching, check_context=lambda: None, cancel_check=lambda: False)
    if entry == "quality":
        auto.read_warehouse_quality(SimpleNamespace(database_path=source, scoring_database_path=static,
                                                    config_dir=config_dir), current, lambda: False)
    elif entry == "first_rules":
        initial.generate_first_rules(**args)
    else:
        preview.build_preview(**args, asset_root=tmp_path, options={})
    assert len(seen) == 1
    assert not seen[0].exists(), "计算结束即释放临时输入"
