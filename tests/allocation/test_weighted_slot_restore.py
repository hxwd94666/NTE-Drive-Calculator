# 验证正式current指针、显式冻结选择、迟到交付和历史来源评分，不运行游戏或外部写入。
from unittest.mock import patch
from threading import Event

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
    preview = runner.restore_weighted_allocation_preview(persisted,
        ownership=s.owner.capture(s.owner.current_identity()))
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
    actual = runner.run_weighted_allocation
    def delayed(request):
        result = actual(request)
        ready.set()
        assert release.wait(30), '真实solve完成后的受控恢复未释放'
        return result
    delivered, errors = [], []
    with patch.object(runner, 'run_weighted_allocation', side_effect=delayed):
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
