# 验证真实回执推进同槽游标、两次替换、撤权与export嵌套事务所有权。
from concurrent.futures import CancelledError
from contextlib import contextmanager
from unittest.mock import patch
import sqlite3

import pytest
from src.features.weighted_allocation import runner
from src.integrations.static_catalog_release import StaticCatalogReleaseError
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataError, UserDataValidationError
from src.storage.sqlite.inventory.inventory_snapshot_usage import exported_snapshot_ids
from src.features.weighted_allocation.save_owner import WeightedOperation
from tests.allocation.explicit_slot_helpers import SlotScenario, app, binding, save_confirm, replacement

NTE_TEST_TIER = 'core'


@pytest.fixture
def scenario(tmp_path):
    application = app()
    value = SlotScenario(tmp_path)
    yield value
    value.close(application)


def test_c8_restored_backup_two_real_replacements_advance_same_slot_receipt(scenario):
    s = scenario
    a, p = s.primary(serial=11, score=33.)
    b, q = s.explicit(serial=11, score=77.)
    before = s.rows()
    _, preview = s.restore({1003: b})
    first = replacement(s.owner, preview, 12)
    assert binding(first).slot_id == b and binding(first).plan_id != q
    assert first.result.profile_id == preview.result.profile_id
    assert first.result.snapshot_id == preview.result.snapshot_id
    first_plan = binding(first).plan_id
    with UserDataDao(s.database) as dao:
        committed = dao.get_loadout_plan(first_plan)
        assert committed['payload']['last_diff']['baseline_plan_id'] == q
    second = replacement(s.owner, first, 13)
    assert binding(second).slot_id == b and binding(second).plan_id != first_plan
    assert second.context is preview.context and second.lock_snapshot is preview.lock_snapshot
    with UserDataDao(s.database) as dao:
        assert dao.get_loadout_slot(a)['current_plan_id'] == p
        assert dao.get_loadout_plan(p) == next(row for row in before[0] if row['plan_id'] == p)
        assert dao.get_loadout_plan(q) == next(row for row in before[0] if row['plan_id'] == q)
        assert dao.get_loadout_plan(first_plan) == committed
        current = dao.get_loadout_slot(b)['current_plan']
        assert current['payload']['last_diff']['baseline_plan_id'] == first_plan
        assert current['source_snapshot_id'] == s.snapshot_id
        assert current['payload']['profile_version'] == s.version
        assert current['payload']['assignment_scores']['nte-core-22-13'] == second.result.unified.selected[0].assignments[0].score
        assert current['is_active'] is False


@pytest.mark.parametrize('first_slot', [False, True])
def test_c9_current_calculation_actual_explicit_save_receipt_then_replace(scenario, first_slot):
    s = scenario
    if first_slot:
        target = None
    else:
        s.primary(serial=11)
        target, _ = s.explicit(serial=12)
    preview = s.calculate()
    saved = save_confirm(s.owner, preview, slot_ids_by_character={1003: target})
    current = binding(saved)
    assert current.slot_id == target if target is not None else current.slot_id > 0
    old_uid = next(a.uid for a in saved.result.unified.selected[0].assignments if a.kind == 'core')
    serial = 11 if old_uid != (22, 11) else 12
    updated = replacement(s.owner, saved, serial)
    new = binding(updated)
    assert new.slot_id == current.slot_id and new.plan_id != current.plan_id
    with UserDataDao(s.database) as dao:
        plan = dao.get_loadout_slot(new.slot_id)['current_plan']
        assert plan['plan_id'] == new.plan_id
        assert plan['payload']['last_diff']['baseline_plan_id'] == current.plan_id


@pytest.mark.parametrize('failure', ['cancel', 'sqlite'])
def test_c9_failed_first_slot_save_never_advances_receipt(scenario, failure):
    s = scenario
    preview = s.calculate()
    before = s.rows()
    actual = WeightedOperation.commit_context
    attempts = []
    @contextmanager
    def stop_at_accept(operation):
        attempts.append(operation)
        if failure == 'cancel':
            operation.cancelled.set()
        else:
            raise sqlite3.OperationalError('受控真实提交前故障')
        with actual(operation):
            yield
    with patch.object(WeightedOperation, 'commit_context', stop_at_accept):
        with pytest.raises(CancelledError if failure == 'cancel' else StaticCatalogReleaseError) as caught:
            save_confirm(s.owner, preview, slot_ids_by_character={1003: None})
    if failure == 'sqlite':
        # 强静态租约保留原SQLite故障为cause；公开包装不改变真实writer的回滚事实。
        assert isinstance(caught.value.__cause__, sqlite3.OperationalError)
        assert str(caught.value.__cause__) == '受控真实提交前故障'
    assert len(attempts) == 1
    assert attempts[0].cancelled.is_set() is (failure == 'cancel')
    assert preview.save_receipt is None and preview.slot_bindings == ()
    assert s.rows() == before


@pytest.mark.parametrize('failure', ['receipt', 'late-pointer', 'cancel'])
def test_c9_post_commit_confirmation_failure_preserves_db_fact_not_old_cursor(scenario, failure):
    from src.services.allocation import weighted_cursor
    s = scenario
    s.primary(serial=11)
    b, q = s.explicit(serial=12)
    _, preview = s.restore({1003: b})
    before = s.rows()
    operation = s.owner.begin_save(preview)
    committed_ids = runner.save_weighted_allocation_preview(preview, ownership=operation)
    assert len(committed_ids) == 1 and committed_ids[0] != q
    if failure == 'receipt':
        with patch.object(weighted_cursor, 'read_weighted_save_receipt', side_effect=RuntimeError('真实commit后回执读取故障')):
            with pytest.raises(RuntimeError, match='回执读取故障'):
                runner.confirm_weighted_allocation_save(preview, committed_ids, ownership=operation)
    elif failure == 'cancel':
        operation.cancelled.set()
        with pytest.raises(CancelledError):
            runner.confirm_weighted_allocation_save(preview, committed_ids, ownership=operation)
    else:
        confirmed = runner.confirm_weighted_allocation_save(preview, committed_ids, ownership=operation)
        s.explicit(b, serial=13)
        with pytest.raises(RuntimeError, match='身份已变化'):
            weighted_cursor.assert_weighted_save_receipt_current(confirmed, operation)
    assert binding(preview).plan_id == q and preview.save_receipt is None
    with UserDataDao(s.database) as dao:
        committed = dao.get_loadout_plan(committed_ids[0])
        assert committed['slot_id'] == b and committed['source_snapshot_id'] == s.snapshot_id
        assert dao.get_loadout_plan(q) == next(row for row in before[0] if row['plan_id'] == q)
        assert len(dao.list_loadout_plans()) == len(before[0]) + (2 if failure == 'late-pointer' else 1)
        if failure in {'receipt', 'cancel'}:
            assert dao.get_loadout_slot(b)['current_plan_id'] == committed_ids[0]
        else:
            assert dao.get_loadout_slot(b)['current_plan_id'] != committed_ids[0]


@pytest.mark.parametrize('changed', ['pointer', 'source', 'target-lock', 'other-lock', 'cancel'])
def test_c10_bound_save_keeps_whole_batch_when_boundary_changes(scenario, changed):
    s = scenario
    a, p = s.primary(serial=11)
    b, q = s.explicit(serial=12)
    if changed == 'target-lock':
        # 正式锁不接受虚拟占位；使用fixture已导入的同形状真实UID，仍保留完整铺板。
        real = s.record(serial=12)
        for assignment in real['assignments']:
            if assignment['kind'] == 'module':
                assignment['uid_slot'] = 21
                assignment['virtual'] = False
                assignment.pop('virtual_equipment')
        real['payload']['assignment_scores'] = {
            f"nte-{item['kind']}-{item['uid_slot']}-{item['uid_serial']}":
            40. if item['kind'] == 'core' else 0. for item in real['assignments']}
        with UserDataDao(s.database) as dao:
            q, = dao.save_plans_to_slots([{**real, 'slot_id': b}])
    _, preview = s.restore({1003: b})
    newer = s.newer_inventory() if changed == 'source' else None
    with UserDataDao(s.database) as dao:
        if changed == 'pointer':
            dao.save_plans_to_slots([{**s.record(serial=13), 'slot_id': b}])
        elif changed == 'source':
            dao.save_plans_to_slots([{**s.record(serial=12, source=newer), 'slot_id': b}])
        elif changed == 'target-lock': dao.set_allocation_lock(q, True)
        elif changed == 'other-lock':
            lock = dao.save_loadout_plan(**s.record(cid=1004, serial=13, core_only=True), is_active=True)
            dao.set_allocation_lock(lock, True)
    before = s.rows()
    if changed == 'cancel': preview.ownership.cancelled.set()
    with pytest.raises((CancelledError, RuntimeError, UserDataValidationError)):
        runner.save_weighted_allocation_preview(preview, ownership=s.owner.begin_save(preview))
    assert s.rows() == before
    assert binding(preview).slot_id == b and binding(preview).plan_id == q
    assert a != b and p != q


def test_c11_real_context_account_aba_never_revives_saved_slot_delivery(scenario):
    s = scenario
    a, original_plan = s.primary()
    persisted, preview = s.restore()
    original = s.context.account
    other = SlotScenario(s.root / 'account-b', account_id='fixture-b')
    try:
        b, other_plan = other.primary()
        assert (a, original_plan, s.snapshot_id, s.profile_id) == (b, other_plan, other.snapshot_id, other.profile_id)
        foreign_before = other.rows()
        s.context.switch_account(other.context.account)
        assert s.owner.current_identity().user_database_path == other.database.resolve()
        s.context.switch_account(original)
        assert other.rows() == foreign_before
    finally:
        other.close(app())
    assert s.context.generation == 2
    newer = s.owner.capture(s.owner.current_identity())
    before = s.rows()
    with pytest.raises(CancelledError):
        runner.restore_weighted_allocation_preview(persisted, ownership=preview.ownership)
    with pytest.raises(CancelledError):
        s.owner.begin_save(preview)
    assert s.owner.accepts(newer) and not s.owner.accepts(preview.ownership)
    assert s.rows() == before


@pytest.mark.parametrize('mode', ['primary-overlay', 'explicit-slots'])
def test_c13_preserves_writer_mode_and_cursor_on_default_restore(scenario, mode):
    s = scenario
    a, p = s.primary(serial=11)
    _a, q = s.explicit(a, serial=12)
    _persisted, preview = s.restore(None if mode == 'primary-overlay' else {1003: a})
    ids = runner.save_weighted_allocation_preview(preview, ownership=s.owner.begin_save(preview))
    with UserDataDao(s.database) as dao:
        current = dao.get_loadout_slot(a)['current_plan']
        assert current['plan_id'] == ids[0]
        assert current['is_active'] is (mode == 'primary-overlay')
        assert current['payload']['last_diff']['baseline_plan_id'] == q
        assert dao.get_loadout_plan(q)['is_active'] is False
        assert dao.get_loadout_plan(p)['payload']['assignment_scores']['nte-core-22-11'] == 40.


@pytest.mark.parametrize('outer', ['none', 'read', 'write', 'savepoint'])
@pytest.mark.parametrize('outcome', ['success', 'validation', 'sqlite', 'cancel'])
def test_export_owns_only_its_successful_begin(scenario, outer, outcome):
    s = scenario
    with UserDataDao(s.database) as dao:
        connection = dao._db()
        original_name = dao.profile()['account_name']
        if outer != 'none':
            connection.execute('BEGIN' if outer == 'read' else 'BEGIN IMMEDIATE')
            if outer in {'write', 'savepoint'}:
                connection.execute("UPDATE database_profile SET account_name='事务尚未提交'")
            if outer == 'savepoint': connection.execute('SAVEPOINT fixture_parent')
        class ExportCancellation(BaseException):
            pass
        error = {'validation': UserDataValidationError('合成校验故障'),
                 'sqlite': sqlite3.OperationalError('合成SQLite故障'),
                 'cancel': ExportCancellation('合成取消')}.get(outcome)
        trace = []
        connection.set_trace_callback(trace.append)
        protected_before = exported_snapshot_ids(s.database)
        actual_items = dao.list_inventory_items
        def missing_item(*args, **kwargs):
            return actual_items(*args, **kwargs)[:-1]
        with patch.object(dao, 'list_inventory_items',
                          side_effect=missing_item if outcome == 'validation' else error,
                          wraps=actual_items if error is None else None):
            if error is None:
                summary, items = dao.export_inventory_snapshot(s.snapshot_id)
                assert summary['stored_item_count'] == len(items) == len(s.raw['params']['items'])
            else:
                # 数量不一致的既有公开错误为UserDataError；缺snapshot才是Validation子类。
                with pytest.raises(UserDataError if outcome in {'sqlite', 'validation'} else type(error)):
                    dao.export_inventory_snapshot(s.snapshot_id)
        connection.set_trace_callback(None)
        transaction_sql = [sql.strip().upper() for sql in trace
                           if sql.strip().upper().startswith(('BEGIN', 'COMMIT', 'ROLLBACK', 'SAVEPOINT', 'RELEASE'))]
        assert transaction_sql == (['BEGIN', 'ROLLBACK'] if outer == 'none' else [])
        assert exported_snapshot_ids(s.database) == (protected_before | {s.snapshot_id}
                                                     if outcome == 'success' else protected_before)
        assert connection.in_transaction is (outer != 'none')
        if outer in {'write', 'savepoint'}:
            assert dao.profile()['account_name'] == '事务尚未提交'
            with sqlite3.connect(s.database.as_uri() + '?mode=ro', uri=True) as fresh:
                assert fresh.execute('SELECT account_name FROM database_profile').fetchone()[0] == original_name
        if outer == 'savepoint': connection.execute('RELEASE SAVEPOINT fixture_parent')
        if outer != 'none': connection.rollback()
