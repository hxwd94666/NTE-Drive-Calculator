# 验证兼顾推荐的来源冻结、互斥身份、取消与独立账号偏好事务，不读取真实账号。
from contextlib import nullcontext
from copy import deepcopy

import pytest

from src.domain.rewind.dual_target import (DualTargetGuard, DualTargetRequest, GroupTarget, RewindSourceChange,
    RewindSourceChanged)
from src.domain.rewind.rewind_loadout import RewindSlotReference
from src.services.rewind.recommendation import RewindShapeRecommendationService
from src.services.rewind.dual_recommendation import guard_payload
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.rewind.test_slot_selection import StaticDao, UserDao, slot_fixture
from tests.rewind.test_rewind_slot_preferences import create_account


def scene(tmp_path):
    user, static = tmp_path / 'user.sqlite3', tmp_path / 'static.sqlite3'
    user.touch(); static.touch()
    dao = UserDao()
    second, plan, items = slot_fixture(2)
    second['character_id'] = plan['character_id'] = 1005
    dao.slots = [slot_fixture()[0], second]
    dao.items.update(items)
    service = RewindShapeRecommendationService(user_database_path=user, static_database_path=static,
        user_dao_factory=lambda *_: dao, static_dao_factory=StaticDao)
    request = DualTargetRequest(GroupTarget((RewindSlotReference(1004, 1, 11),), 'A'),
                                GroupTarget((RewindSlotReference(1005, 2, 12),), 'SSS'))
    return service, dao, request


def test_two_targets_read_only_their_frozen_source_scores(tmp_path):
    service, dao, request = scene(tmp_path)
    before = deepcopy(dao.slots)
    result = service.analyze_dual_target_shortfalls(request)
    assert result.strategy == 'dual_target' and result.required_count == 10
    assert result.recommendations[0].quantity == 8
    assert result.plans[0].total_cost == 360
    assert result.dual_target.groups[0].items[0].target_score == 16
    assert result.dual_target.groups[1].items[0].target_score == 28
    assert result.selected_source_snapshots == ((1, 3), (2, 3))
    assert {snapshot for snapshot, _ in dao.queries} == {3, 9}
    assert dao.slots == before


@pytest.mark.parametrize('problem', ['archived', 'missing_score', 'source', 'virtual', 'cross_role', 'duplicate_uid'])
def test_invalid_inputs_never_form_a_result(tmp_path, problem):
    service, dao, request = scene(tmp_path)
    plan = dao.slots[1]['current_plan']
    if problem == 'archived': dao.slots[1]['is_archived'] = True
    elif problem == 'missing_score': plan['payload']['assignment_scores'].clear()
    elif problem == 'source': plan['source_snapshot_id'] = None
    elif problem == 'virtual': plan['assignments'][0]['raw_assignment'] = {'virtual': True}
    elif problem == 'cross_role': dao.slots[1]['character_id'] = 1004
    else:
        plan['assignments'] = deepcopy(dao.slots[0]['current_plan']['assignments'])
        plan['payload']['assignment_scores'] = deepcopy(dao.slots[0]['current_plan']['payload']['assignment_scores'])
    with pytest.raises(ValueError):service.analyze_dual_target_shortfalls(request)


@pytest.mark.parametrize('problem', ['plan_replaced', 'slot_state'])
def test_slot_problems_name_the_role_instead_of_its_id(tmp_path, problem):
    service, dao, request = scene(tmp_path)
    plan = dao.slots[0]['current_plan']
    if problem == 'plan_replaced': plan['plan_id'] = 99
    else: plan['payload']['assignment_scores'].clear()
    with pytest.raises(ValueError) as error:
        service.analyze_dual_target_shortfalls(request)
    assert '测试' in str(error.value) and '1004' not in str(error.value)


def test_inventory_or_plan_changes_and_cancellation_invalidate_after_read(tmp_path):
    service, dao, request = scene(tmp_path)
    result = service.analyze_dual_target_shortfalls(request)
    dao.current_inventory_snapshot_id = lambda: 10
    with pytest.raises(RewindSourceChanged, match='库存') as error:
        service.validate_dual_target_guard(result.dual_guard)
    assert error.value.changes == {RewindSourceChange('inventory')}
    dao.current_inventory_snapshot_id = lambda: 9
    dao.slots[0]['current_plan']['source_snapshot_id'] = 99
    with pytest.raises(RewindSourceChanged, match='配装') as error:
        service.validate_dual_target_guard(result.dual_guard)
    assert error.value.changes == {RewindSourceChange('source_unavailable', 1004, 1)}
    def cancel():raise InterruptedError('cancel')
    with pytest.raises(InterruptedError):service.analyze_dual_target_shortfalls(request, checkpoint=cancel)


def test_confirmation_covers_only_listed_changes_and_does_not_rebind_the_saved_source(tmp_path):
    service, dao, request = scene(tmp_path)
    analysis = service.analyze_dual_target_shortfalls(request)
    guard = analysis.dual_guard
    original = deepcopy(guard_payload(guard))
    dao.current_inventory_snapshot_id = lambda: 10
    with pytest.raises(RewindSourceChanged) as error:
        service.validate_dual_target_guard(guard)
    accepted = error.value.changes
    service.validate_dual_target_guard(guard, accepted_changes=accepted)
    assert guard_payload(guard) == original
    # 一次执行允许继续不会改变服务默认策略，也不读取新库存来补造推荐。
    with pytest.raises(RewindSourceChanged):
        service.validate_dual_target_guard(guard)
    # 确认后才出现的配装更新不在本次许可内，须重新确认。
    dao.slots[1]['current_plan']['plan_id'] += 1
    with pytest.raises(RewindSourceChanged) as error:
        service.validate_dual_target_guard(guard, accepted_changes=accepted)
    assert error.value.changes == accepted | {RewindSourceChange('plan_replaced', 1005, 2)}


@pytest.mark.parametrize('problem,kind', [('plan', 'plan_replaced'), ('archived', 'slot_unavailable'),
                                          ('character', 'slot_unavailable'), ('missing', 'slot_unavailable'),
                                          ('plan_source', 'source_unavailable')])
def test_loadout_updates_are_listed_for_confirmation(tmp_path, problem, kind):
    service, dao, request = scene(tmp_path)
    guard = service.analyze_dual_target_shortfalls(request).dual_guard
    if problem == 'plan':
        dao.slots[0]['current_plan']['plan_id'] += 1
    elif problem == 'archived':
        dao.slots[0]['is_archived'] = True
    elif problem == 'character':
        dao.slots[0]['character_id'] += 100
    elif problem == 'missing':
        dao.slots.pop(0)
    else:
        dao.slots[0]['current_plan']['source_snapshot_id'] = 99
    with pytest.raises(RewindSourceChanged) as error:
        service.validate_dual_target_guard(guard)
    assert error.value.changes == {RewindSourceChange(kind, 1004, 1)}
    service.validate_dual_target_guard(guard, accepted_changes=error.value.changes)


def test_pruned_generation_snapshot_is_not_a_change(tmp_path):
    service, dao, request = scene(tmp_path)
    guard = service.analyze_dual_target_shortfalls(request).dual_guard
    original = dao.inventory_snapshot_summary
    # 生成时快照只记身份；保留策略清理它不应永久阻止执行。
    dao.inventory_snapshot_summary = lambda snapshot: None if snapshot == guard.snapshot_id else original(snapshot)
    service.validate_dual_target_guard(guard)


@pytest.mark.parametrize('problem', ['static', 'cancel'])
def test_confirmation_never_overrides_static_or_cancellation(tmp_path, monkeypatch, problem):
    service, dao, request = scene(tmp_path)
    guard = service.analyze_dual_target_shortfalls(request).dual_guard
    dao.current_inventory_snapshot_id = lambda: 10
    accepted = frozenset({RewindSourceChange('inventory')})
    checkpoint = lambda: None
    if problem == 'static':
        monkeypatch.setattr(service, 'static_identity', lambda: ('different', 0, 0))
    else:
        def checkpoint():
            raise InterruptedError('cancel')
    with pytest.raises((ValueError, InterruptedError)) as error:
        service.validate_dual_target_guard(guard, checkpoint=checkpoint, accepted_changes=accepted)
    assert not isinstance(error.value, RewindSourceChanged)


def test_second_consistent_read_rejects_replaced_plan_before_delivery(tmp_path):
    service, dao, request = scene(tmp_path)
    count = 0
    def read():
        nonlocal count
        count += 1
        if count == 2:dao.slots[1]['current_plan']['plan_id'] += 1
        return nullcontext()
    dao.read_consistent_state = read
    with pytest.raises(ValueError, match='配装'):service.analyze_dual_target_shortfalls(request)


def test_dual_preferences_and_plan_save_are_atomic_and_account_private(tmp_path):
    user, other, static = tmp_path / 'one.sqlite3', tmp_path / 'two.sqlite3', tmp_path / 'static.sqlite3'
    ref, _ = create_account(user, 'one');create_account(other, 'two');static.touch()
    service = RewindShapeRecommendationService(user_database_path=user, static_database_path=static)
    service.save_preferences({'strategy': 'balanced', 'target_grade': 'S', 'saved_rewind_shape_ids': ['EquipmentGeometry_Hen2'] * 8}, save_plan=True)
    groups = {'improve': dict(slots={'1004': ref.slot_id}, grade='A', mode='grade', custom_percent=None),
              'push': dict(slots={}, grade='SSS', mode='grade', custom_percent=None)}
    service.save_dual_target_preferences({'groups': groups})
    with UserDataDao(user) as dao:
        snapshot = dao.current_inventory_snapshot_id()
    guard = DualTargetGuard(snapshot, service.static_identity(), ((ref, snapshot),))
    service.save_dual_target_preferences({'saved_rewind_shape_ids': ['EquipmentGeometry_Hen4'] * 8}, guard=guard)
    saved = service.load_dual_target_preferences()
    assert saved['groups'] == groups and saved['saved_guard'] == guard_payload(guard)
    assert service.load_preferences()['saved_rewind_shape_ids'] == ['EquipmentGeometry_Hen2'] * 8
    second = RewindShapeRecommendationService(user_database_path=other, static_database_path=static)
    assert 'saved_rewind_shape_ids' not in second.load_dual_target_preferences()
    bad = deepcopy(groups);bad['push']['slots']['1004'] = ref.slot_id
    with pytest.raises(ValueError):service.save_dual_target_preferences({'groups': bad})
    assert service.load_dual_target_preferences() == saved
    def cancel():raise InterruptedError('cancel')
    with pytest.raises(InterruptedError):service.save_dual_target_preferences({'other': 1}, checkpoint=cancel)
    assert service.load_dual_target_preferences() == saved
    calls = 0
    def late_cancel():
        nonlocal calls
        calls += 1
        if calls == 2:raise InterruptedError('cancel after patch')
    with pytest.raises(InterruptedError):service.save_dual_target_preferences({'other': 2}, checkpoint=late_cancel)
    assert service.load_dual_target_preferences() == saved
    static_changed = DualTargetGuard(snapshot, ('changed', 0, 0), ((ref, snapshot),))
    with pytest.raises(ValueError):service.save_dual_target_preferences({'saved_rewind_shape_ids': ['EquipmentGeometry_Hen2'] * 8}, guard=static_changed)
    assert service.load_dual_target_preferences() == saved
    for ids in [[], ['missing'] * 8, ['EquipmentGeometry_Hen2'] * 9]:
        with pytest.raises(ValueError):service.save_dual_target_preferences({'saved_rewind_shape_ids': ids}, guard=guard)
        assert service.load_dual_target_preferences() == saved
    # 生成后所选配装或库存已更新：保存照常覆盖，并原样记录生成时的冻结来源。
    stale = DualTargetGuard(snapshot + 100, service.static_identity(),
                            ((RewindSlotReference(1004, ref.slot_id, ref.plan_id + 1), snapshot),))
    service.save_dual_target_preferences({'saved_rewind_shape_ids': ['EquipmentGeometry_Hen2'] * 8}, guard=stale)
    overwritten = service.load_dual_target_preferences()
    assert overwritten['saved_rewind_shape_ids'] == ['EquipmentGeometry_Hen2'] * 8
    assert overwritten['saved_guard'] == guard_payload(stale)
