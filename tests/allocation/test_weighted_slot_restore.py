# 验证正式current指针、显式冻结选择、迟到交付和历史来源评分，不运行游戏或外部写入。
from unittest.mock import patch
from threading import Event
from dataclasses import replace
from concurrent.futures import CancelledError
import os

import pytest
from src.features.weighted_allocation import runner
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.allocation.explicit_slot_helpers import SlotScenario, app, binding, pump

NTE_TEST_TIER = 'core'


@pytest.fixture
def scenario(tmp_path):
    application = app()
    value = SlotScenario(tmp_path)
    yield value
    value.close(application)


def test_c1_default_restores_current_not_historical_active(scenario):
    s = scenario
    slot, old = s.primary(serial=11, score=40.)
    _slot, current = s.explicit(slot, serial=12, score=55.)
    before = s.rows()
    persisted = s.read()
    assert [p.plan_id for p in persisted.saved_plans] == [current]
    # 正式恢复仍物化 SQLite/context/static pin；这里只阻断不应出现的选优入口。
    with patch.object(runner, 'solve_allocation_context', side_effect=AssertionError('restore must not solve')) as solver:
        preview = runner.restore_weighted_allocation_preview(persisted,
            ownership=s.owner.capture(s.owner.current_identity()))
    solver.assert_not_called()
    assert binding(preview).slot_id == slot and binding(preview).plan_id == current
    assert preview.result.unified.selected[0].assignments[0].uid == (22, 12)
    assert preview.result.unified.selected[0].score == 55.
    assert s.rows() == before
    assert next(row for row in before[0] if row['plan_id'] == old)['is_active'] is True
    assert next(row for row in before[0] if row['plan_id'] == current)['is_active'] is False


def test_c2_public_restore_uses_current_explicit_primary_and_new_owner(scenario):
    s = scenario
    with UserDataDao(s.database) as dao:
        slot = dao.create_loadout_slot(1003, '正式主力', slot_key='primary')
    _, current = s.explicit(slot, score=17.)
    before = s.rows()
    _, preview = s.restore()
    assert preview is not None
    assert binding(preview).plan_id == current
    assert preview.ownership.identity.generation == s.context.generation
    assert s.rows() == before


def test_c3_explicit_selection_is_copied_and_uses_id_not_label(scenario):
    s = scenario
    primary, p = s.primary()
    backup, q = s.explicit(serial=12, score=71.)
    selection = {1003: backup}
    persisted = s.read(selection)
    selection[1003] = primary
    with UserDataDao(s.database) as dao:
        dao.rename_loadout_slot(backup, '主力同名展示')
    preview = runner.restore_weighted_allocation_preview(persisted,
        ownership=s.owner.capture(s.owner.current_identity()))
    assert binding(preview).slot_id == backup and binding(preview).plan_id == q
    assert [row.plan_id for row in s.read().saved_plans] == [p]


@pytest.mark.parametrize('invalid_primary', ['empty', 'profile'])
def test_c4_default_never_falls_back_to_good_backup(scenario, invalid_primary):
    s = scenario
    primary, p = s.primary()
    s.explicit(serial=12)
    with UserDataDao(s.database) as dao:
        if invalid_primary == 'empty':
            dao.deactivate_loadout_plan(p)
        else:
            record = s.record()
            record['payload']['profile_version'] += 1
            dao.save_plans_to_slots([{**record, 'slot_id': primary}])
    before = s.rows()
    persisted = s.read()
    assert persisted.restore_request is None and persisted.saved_plans == ()
    assert persisted.characters
    assert s.rows() == before


@pytest.mark.parametrize('bad', ['empty', 'incomplete', 'wrong-role', 'duplicate', 'archived', 'empty-slot', 'plan-slot'])
def test_c5_invalid_explicit_mapping_never_partial_or_default(tmp_path, bad):
    application = app()
    s = SlotScenario(tmp_path, roles=(1003, 1004))
    try:
        a, _ = s.primary()
        b, _ = s.explicit(cid=1004, serial=12)
        mapping = {1003: a, 1004: b}
        with UserDataDao(s.database) as dao:
            if bad == 'empty': mapping = {}
            elif bad == 'incomplete': mapping = {1003: a}
            elif bad == 'wrong-role': mapping = {1003: b, 1004: a}
            elif bad == 'duplicate': mapping = {1003: a, 1004: a}
            elif bad == 'archived': dao.archive_loadout_slot(b)
            elif bad == 'empty-slot': dao.deactivate_loadout_plan(dao.get_loadout_slot(b)['current_plan_id'])
            else:
                # 特意模拟关联损坏；不把这种SQL作为正常fixture创建API。
                dao._db().execute('UPDATE loadout_plan SET slot_id=? WHERE plan_id=?',
                    (a, dao.get_loadout_slot(b)['current_plan_id']))
                dao._db().commit()
        before = s.rows()
        persisted = s.read(mapping)
        assert persisted.restore_request is None and not persisted.saved_plans
        assert len(persisted.characters) == 2
        assert s.rows() == before
    finally:
        s.close(application)


@pytest.mark.parametrize('changed', ['current-pointer', 'selection'])
def test_c6_inflight_restore_rechecks_current_pointer_and_cancelled_selection(scenario, changed):
    from src.app.workers import WorkerThread
    s = scenario
    a, _ = s.primary()
    b, _ = s.explicit(serial=12)
    persisted = s.read({1003: b})
    operation = s.owner.capture(s.owner.current_identity())
    ready, release = Event(), Event()
    actual = runner._restore_saved_option
    def delayed(context, signature):
        result = actual(context, signature)
        ready.set()
        assert release.wait(30), '真实历史布局校验后的受控恢复未释放'
        return result
    delivered, errors = [], []
    with patch.object(runner, '_restore_saved_option', side_effect=delayed):
        worker = WorkerThread(target=lambda: runner.restore_weighted_allocation_preview(persisted, ownership=operation))
        worker.result_ready.connect(delivered.append)
        worker.error.connect(errors.append)
        s.owner.track(worker)
        worker.start()
        try:
            pump(app(), ready.is_set)
            if changed == 'current-pointer':
                s.explicit(b, serial=13)
            else:
                # 选择改为a时，调用方撤销旧请求；复制后的b输入从不被原地改写。
                operation.cancelled.set()
                selected = s.read({1003: a})
                assert selected.selected_slots == ((1003, a),)
                assert persisted.selected_slots == ((1003, b),)
            before = s.rows()
        finally:
            release.set()
        pump(app(), s.owner.try_retire)
    assert delivered == [] and len(errors) == 1
    assert ('身份已变化' if changed == 'current-pointer' else '已取消') in errors[0]
    assert s.rows() == before


def test_c7_selected_source_survives_new_inventory_bad_unselected_and_global_lock(scenario):
    s = scenario
    a, _ = s.primary(serial=11)
    latest = s.newer_inventory()
    assert latest != s.snapshot_id
    b, _ = s.explicit(serial=12)
    with UserDataDao(s.database) as dao:
        record = s.record(serial=13)
        record['source_snapshot_id'] = None
        bad_slot = dao.create_loadout_slot(1003, '缺来源未选备用')
        dao.save_plans_to_slots([{**record, 'slot_id': bad_slot}])
        lock = dao.save_loadout_plan(**s.record(cid=1004, serial=13, core_only=True), is_active=True)
        dao.set_allocation_lock(lock, True)
    before = s.rows()
    _, preview = s.restore({1003: b})
    assert preview.result.snapshot_id == s.snapshot_id
    assert binding(preview).slot_id == b
    assert preview.result.unified.selected[0].score == 40.
    assert 'nte-core-22-13' in preview.lock_snapshot.reserved_uids
    assert (22, 13) not in {candidate.uid for candidate in preview.context.candidates}
    assert s.rows() == before
    assert a != b


@pytest.mark.parametrize('explicit', [False, True])
def test_c7_mixed_selected_sources_reject_whole_restore(tmp_path, explicit):
    application = app()
    s = SlotScenario(tmp_path, roles=(1003, 1004))
    try:
        a, _ = s.primary()
        newer = s.newer_inventory()
        b, _ = s.primary(cid=1004, serial=12, source=newer)
        before = s.rows()
        persisted = s.read({1003: a, 1004: b} if explicit else None)
        assert persisted.restore_request is None and persisted.saved_plans == ()
        assert len(persisted.characters) == 2
        assert s.rows() == before
    finally:
        s.close(application)


@pytest.mark.parametrize('missing', ['scores', 'source'])
def test_c12_missing_not_zero_while_real_zero_and_virtual_restore(scenario, missing):
    s = scenario
    a, _ = s.primary(score=0.)
    _, preview = s.restore()
    assert preview.result.unified.selected[0].score == 0.
    s.explicit(a, serial=901, score=0., virtual=True)
    _, virtual = s.restore()
    item = virtual.result.unified.selected[0].assignments[0]
    assert item.virtual and item.uid[0] == 0 and item.score == 0.
    with UserDataDao(s.database) as dao:
        record = s.record(missing=missing)
        if missing == 'source': record['source_snapshot_id'] = None
        dao.save_plans_to_slots([{**record, 'slot_id': a}])
    before = s.rows()
    persisted = s.read()
    assert persisted.restore_request is None
    assert s.rows() == before


def test_restore_missing_core_retains_saved_score_and_truthful_metadata(scenario):
    s = scenario
    a, _ = s.primary()
    record = s.record(score=0.)
    record['assignments'] = [row for row in record['assignments'] if row['kind'] == 'module']
    with UserDataDao(s.database) as dao:
        dao.save_plans_to_slots([{**record, 'slot_id': a}])
    before = s.rows()
    _, preview = s.restore()
    option = preview.result.unified.selected[0]
    assert option.score == 0. and option.missing_core_reason == '保存方案未包含卡带'
    assert option.rank == 1 and option.priority_values == () and preview.result.role_top_k == ()
    assert all(item.virtual and item.score == 0. for item in option.assignments)
    assert 'ScoringEngine' not in option.satisfied_constraints
    assert s.rows() == before


@pytest.mark.parametrize('invalid', ['duplicate', 'two-core', 'virtual-real-uid', 'virtual-score',
                                    'nonfinite', 'overlap', 'missing-candidate'])
def test_restore_validates_saved_facts_not_an_unrelated_new_solution(scenario, invalid):
    s = scenario
    s.primary()
    persisted = s.read()
    signature = persisted.saved_plans[0]
    assignments = list(signature.assignments)
    if invalid == 'duplicate':
        assignments.append(assignments[0])
    elif invalid == 'two-core':
        assignments.append(replace(assignments[0], uid=(22, 12)))
    elif invalid == 'virtual-real-uid':
        assignments[0] = replace(assignments[0], virtual=True, score=0.)
    elif invalid == 'virtual-score':
        assignments[1] = replace(assignments[1], score=1.)
    elif invalid == 'nonfinite':
        assignments[0] = replace(assignments[0], score=float('nan'))
    elif invalid == 'overlap':
        assignments[2] = replace(assignments[2], geometry=assignments[1].geometry,
            target_row=assignments[1].target_row, target_column=assignments[1].target_column)
    else:
        assignments[0] = replace(assignments[0], uid=(22, 999))
    signature = replace(signature, assignments=tuple(assignments), uids=frozenset(a.uid for a in assignments))
    persisted = replace(persisted, saved_plans=(signature,))
    before = s.rows()
    with pytest.raises(RuntimeError):
        runner.restore_weighted_allocation_preview(persisted,
            ownership=s.owner.capture(s.owner.current_identity()))
    assert s.rows() == before


def test_restore_rejects_cross_role_real_uid_reuse(tmp_path):
    application = app()
    s = SlotScenario(tmp_path, roles=(1003, 1004))
    try:
        s.primary(serial=11)
        s.primary(cid=1004, serial=12)
        persisted = s.read()
        first, second = persisted.saved_plans
        assignments = (replace(second.assignments[0], uid=first.assignments[0].uid), *second.assignments[1:])
        second = replace(second, assignments=assignments, uids=frozenset(a.uid for a in assignments))
        before = s.rows()
        with pytest.raises(RuntimeError, match='重复使用真实装备'):
            runner.restore_weighted_allocation_preview(replace(persisted, saved_plans=(first, second)),
                ownership=s.owner.capture(s.owner.current_identity()))
        assert s.rows() == before
    finally:
        s.close(application)


@pytest.mark.parametrize('changed', ['global-lock', 'generation'])
def test_restore_rechecks_late_lock_and_owner_without_solver(scenario, changed):
    s = scenario
    s.primary()
    persisted = s.read()
    actual = runner._restore_saved_option
    before = []
    def delayed(context, signature):
        result = actual(context, signature)
        if changed == 'global-lock':
            with UserDataDao(s.database) as dao:
                lock = dao.save_loadout_plan(**s.record(cid=1004, serial=13, core_only=True), is_active=True)
                dao.set_allocation_lock(lock, True)
        else:
            # 注入已经推进的组合根代次；现有 c11 另覆盖真实账号 ABA 切换。
            s.context._generation += 1
        before.append(s.rows())
        return result
    with patch.object(runner, '_restore_saved_option', side_effect=delayed):
        with pytest.raises(CancelledError if changed == 'generation' else RuntimeError):
            runner.restore_weighted_allocation_preview(persisted,
                ownership=s.owner.capture(s.owner.current_identity()))
    assert len(before) == 1 and s.rows() == before[0]


@pytest.mark.parametrize('changed', ['pin', 'dataset'])
def test_restore_preserves_static_identity_and_pin(scenario, tmp_path, changed):
    from tests.allocation.static_release_helpers import clone_test_release
    s = scenario
    database, release = clone_test_release(tmp_path / 'release')
    s.primary()
    persisted = runner.read_weighted_allocation_persistence(s.database, database, True)
    before = s.rows()
    if changed == 'dataset':
        persisted = replace(persisted, static_dataset=replace(persisted.static_dataset, dataset_id='other'))
        with pytest.raises(RuntimeError, match='数据集'):
            runner.restore_weighted_allocation_preview(persisted,
                ownership=s.owner.capture(s.owner.current_identity()))
    else:
        actual = runner._restore_saved_option
        guarded = []
        def while_pinned(context, signature):
            # 实际克隆文件仅尝试取写句柄，无写入；恢复期间应由正式静态 pin 拒绝。
            with pytest.raises(PermissionError):
                descriptor = os.open(database, os.O_WRONLY)
                os.close(descriptor)
            guarded.append(True)
            return actual(context, signature)
        with patch.object(runner, '_restore_saved_option', side_effect=while_pinned):
            preview = runner.restore_weighted_allocation_preview(persisted,
                ownership=s.owner.capture(s.owner.current_identity()))
        assert guarded == [True] and preview.static_release == release
        descriptor = os.open(database, os.O_WRONLY)
        os.close(descriptor)  # owner 退出已释放 pin，仍不写候选文件。
    assert s.rows() == before
