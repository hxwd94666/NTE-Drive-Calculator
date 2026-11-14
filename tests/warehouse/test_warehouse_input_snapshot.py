# 验证仓库专用输入不搬历史库，保留完整当前库存、旧来源锁、取消和单事务一致性。
from concurrent.futures import CancelledError
from contextlib import closing
from dataclasses import FrozenInstanceError
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from src.domain.equipment.eligibility.management_settings import MatchingScope
from src.domain.equipment.warehouse.calculation_input import WarehouseCalculationInput
from src.domain.equipment.warehouse.warehouse_decisions import DecisionStatus, WarehouseDecision
from src.domain.official_role.models import WORLD_BONUS_SETTING_KEY
from src.services.inventory.recommendation import warehouse_calculation_inputs as module
from src.services.inventory.recommendation.warehouse_predecessors import PredecessorBatch
from src.storage.sqlite.inventory.warehouse_recommendation_inputs import recommendation_account_identity
from src.storage.sqlite.user_data_dao import UserDataDao
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from tests.storage.test_user_data_inventory_dao import item
from tests.storage.test_user_data_inventory_dao import snapshot
from tests.storage.test_user_data_inventory_dao import stat


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / 'data/game_static.sqlite3'
CONFIG = ROOT / 'config'


@pytest.fixture
def account(tmp_path, monkeypatch):
    path = tmp_path / 'source.sqlite3'
    with StaticGameDataDao(STATIC) as static:
        geometry = static.get_equipment_item(item(1, 1)['item_id'])['geometry_id']
    def valid_item(serial, slot):
        return {**item(serial, slot), 'geometry': geometry,
                'main_stats': [stat('AtkUp', 0.1, True), stat('HPMaxUp', 0.1, True)]}
    with UserDataDao(path, account_id='warehouse-test') as dao:
        first = dao.import_inventory_snapshot(snapshot(1, [valid_item(11, 21)]))
        current = dao.import_inventory_snapshot(snapshot(2, [valid_item(13, 23)]))
        slot = dao.create_loadout_slot(1003, 'locked')
        plan = dao.save_plan_to_slot(slot, name='locked', source_snapshot_id=first,
            assignments=[{'uid_serial': 11, 'uid_slot': 21, 'kind': 'module'}],
            payload={'schema': 'allocation-official-snapshot-v1', 'source_role_name': 'role'})
        dao.set_allocation_lock(plan, True)
    # 本模块测试账号读取边界；真实全角色目录及算法的新旧对照另有离线证据。
    def catalog(**kwargs):
        assert kwargs['user_read_unit'].database_path == path
        return SimpleNamespace(roles_db={}, sets_db={}, shapes_db={}, board_matrices={}, fork_ids_by_name={})
    monkeypatch.setattr(module, 'build_legacy_allocation_static_catalog', catalog)
    return path, first, current, plan


def freeze(path, **kwargs):
    return module.freeze_warehouse_calculation_inputs(path, STATIC, CONFIG, **kwargs)


def test_current_inventory_and_old_source_locks_are_frozen_without_a_compatibility_database(account):
    path, first, current, _ = account
    with UserDataDao.read_only(path) as user:
        originals = user.list_inventory_items_with_runtime_state(current)
        owners = user.list_allocation_locked_equipment_owners()
        before = user.export_inventory_snapshot(first)
    result = freeze(path)
    assert isinstance(result, WarehouseCalculationInput)
    assert result.snapshot_id == current
    assert result.values()['originals'] == originals
    assert result.values()['locked_owners'] == owners
    assert owners[0]['source_snapshot_id'] == first
    assert {row['uid_serial'] for row in result.values()['originals']} == {13}
    assert result.account_identity == recommendation_account_identity(path)
    with UserDataDao.read_only(path) as user:
        assert user.export_inventory_snapshot(first) == before
        assert user.current_inventory_snapshot_id() == current
    assert [p.name for p in path.parent.glob('*.sqlite3')] == ['source.sqlite3']


def test_frozen_nested_values_cannot_be_mutated_by_another_consumer(account):
    path, _, _, _ = account
    result = freeze(path)
    first = result.values()
    assert 'nte-module' not in repr(result)
    first['originals'][0]['sub_stats'][0]['value'] = 999
    first['config']['skip_scoring'] = not first['config']['skip_scoring']
    assert result.values() != first
    with pytest.raises(FrozenInstanceError):
        result.snapshot_id = 999


def test_reads_use_one_transaction_while_writers_continue(account, monkeypatch):
    path, _, current, _ = account
    original = module.build_legacy_allocation_static_catalog
    seen = []
    def concurrent(**kwargs):
        # 事务已读取当前库存、配置及指纹，此时写入并提交不应污染该输入。
        with UserDataDao(path) as writer:
            writer.import_inventory_snapshot(snapshot(3, [item(99, 99)]))
            writer.replace_application_setting_copy(WORLD_BONUS_SETTING_KEY, {'changed': True})
        user = kwargs['user_read_unit']
        seen.append(user.current_inventory_snapshot_id())
        assert WORLD_BONUS_SETTING_KEY not in user.list_application_setting_copies()
        return original(**kwargs)
    monkeypatch.setattr(module, 'build_legacy_allocation_static_catalog', concurrent)
    result = freeze(path)
    assert seen == [current]
    assert result.snapshot_id == current
    assert result.values()['originals'][0]['uid_serial'] == 13
    assert result.account_identity != recommendation_account_identity(path)


def test_explicit_page_snapshot_does_not_change_live_current_pointer(account):
    path, first, current, _ = account
    result = freeze(path, snapshot_id=first)
    assert result.snapshot_id == first
    assert result.values()['originals'][0]['uid_serial'] == 11
    with UserDataDao.read_only(path) as dao:
        assert dao.current_inventory_snapshot_id() == current


def test_cancel_missing_and_partial_inputs_do_not_write_account(account, monkeypatch):
    path, _, current, _ = account
    before = recommendation_account_identity(path)
    with pytest.raises(CancelledError):
        freeze(path, cancel_check=lambda: True)
    with pytest.raises(ValueError, match='完整库存'):
        freeze(path, snapshot_id=999999)
    original = module.build_legacy_allocation_static_catalog
    cancelled = [False]
    def cancel_during_read(**kwargs):
        value = original(**kwargs)
        cancelled[0] = True
        return value
    monkeypatch.setattr(module, 'build_legacy_allocation_static_catalog', cancel_during_read)
    with pytest.raises(CancelledError):
        freeze(path, cancel_check=lambda: cancelled[0])
    assert recommendation_account_identity(path) == before
    with UserDataDao.read_only(path) as user:
        assert user.current_inventory_snapshot_id() == current
    assert [p.name for p in path.parent.glob('*.sqlite3')] == ['source.sqlite3']


def test_read_only_dao_never_migrates_and_rejects_writes(account, monkeypatch):
    path, _, _, _ = account
    monkeypatch.setattr(UserDataDao, '_migrate_schema', lambda *a, **k: pytest.fail('冻结不迁移来源'))
    with UserDataDao.read_only(path) as user:
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            user._db().execute("UPDATE database_profile SET account_name='changed'")
    assert freeze(path).values()['originals']


def test_unrelated_history_growth_does_not_change_frozen_business_input(account):
    path, _, _, plan = account
    before = freeze(path)
    with closing(sqlite3.connect(path)) as db, db:
        for index in range(500):
            db.execute('INSERT INTO loadout_plan(name,character_id,source_snapshot_id,status,score,payload_json,'
                       'is_active,allocation_locked,created_at_utc,updated_at_utc) '
                       "SELECT ?,character_id,source_snapshot_id,status,score,payload_json,0,0,created_at_utc,updated_at_utc "
                       'FROM loadout_plan WHERE plan_id=?', (f'archived-{index}', plan))
        db.execute('CREATE TABLE unrelated_history(payload BLOB)')
        db.execute('INSERT INTO unrelated_history VALUES(zeroblob(1048576))')
    after = freeze(path)
    assert after == before
    assert len(after.payload) == len(before.payload)


def test_current_locks_native_observations_and_world_bonus_remain_in_invalidation_domain(account):
    path, _, _, plan = account
    before = recommendation_account_identity(path)
    with UserDataDao(path) as user:
        user.set_allocation_lock(plan, False)
    unlocked = recommendation_account_identity(path)
    assert before != unlocked
    with UserDataDao(path) as user:
        user.replace_application_setting_copy(WORLD_BONUS_SETTING_KEY, {'test': 1})
    world = recommendation_account_identity(path)
    assert world != unlocked
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('INSERT INTO character_profile_observation(character_id,character_level,breakthrough_stage,cultivation_json,updated_at_utc) '
                   "VALUES(1003,20,0,'{}','test')")
    assert recommendation_account_identity(path) != world


def test_only_active_latest_preference_versions_affect_identity(account):
    path, _, _, _ = account
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO optimization_preference_profile(profile_id,name,is_active,created_at_utc,updated_at_utc) "
                   "VALUES(100,'test',1,'a','a')")
        for version in (1, 2):
            cursor = db.execute('INSERT INTO optimization_preference_version(profile_id,version_number,allocation_strategy,created_at_utc) '
                                "VALUES(100,?,'role_priority','a')", (version,))
            db.execute('INSERT INTO optimization_preference_character(profile_version_id,character_id,ordinal) VALUES(?,1003,0)',
                       (cursor.lastrowid,))
    before = recommendation_account_identity(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('UPDATE optimization_preference_character SET priority_group=2 WHERE profile_version_id IN '
                   '(SELECT profile_version_id FROM optimization_preference_version WHERE version_number=1)')
    assert recommendation_account_identity(path) == before
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('UPDATE optimization_preference_character SET priority_group=2 WHERE profile_version_id IN '
                   '(SELECT profile_version_id FROM optimization_preference_version WHERE version_number=2)')
    assert recommendation_account_identity(path) != before


@pytest.mark.parametrize('entry', ['quality', 'first_rules', 'preview'])
def test_all_three_entries_pass_the_frozen_object_to_consumers(account, monkeypatch, entry):
    from src.services.inventory.recommendation import warehouse_auto_quality as auto
    from src.services.inventory.recommendation import warehouse_quality_assessment as initial
    from src.services.inventory.recommendation import warehouse_recommendation_preview as preview
    from src.services.identification.matching_settings import MatchingSettingsService
    path, _, current, _ = account
    config = preview.post_config(path)
    matching = MatchingSettingsService(path, scope=MatchingScope.WAREHOUSE).load()
    seen = []
    def evaluate(inputs, *args, **kwargs):
        assert isinstance(inputs, WarehouseCalculationInput)
        assert inputs.snapshot_id == current
        assert len(inputs.values()['originals']) == 1
        seen.append(inputs)
        return {'score': 10, 'snapshot_id': current, 'recommended_drive_grade': 'SS',
                'drive_rate': 1, 'drive_count': 1, 'elapsed_seconds': 0}
    def predecessors(inputs, *args, **kwargs):
        evaluate(inputs)
        raw = {f"nte-{row['kind']}-{row['uid_slot']}-{row['uid_serial']}": row
               for row in inputs.values()['originals']}
        decisions = {uid: WarehouseDecision(uid, DecisionStatus.UNDECIDED, 'manual') for uid in raw}
        return PredecessorBatch(current, tuple(SimpleNamespace(uid=uid) for uid in raw), raw, decisions)
    monkeypatch.setattr(auto, 'assess_quality', evaluate)
    monkeypatch.setattr(initial, 'assess_quality', evaluate)
    monkeypatch.setattr(initial, 'generate_warehouse_rules', lambda *a, **k: {'rules': [], 'options': {}})
    monkeypatch.setattr(preview, 'evaluate_predecessors', predecessors)
    args = dict(database=path, static_database=STATIC, config_dir=CONFIG, config=config,
                matching=matching, check_context=lambda: None, cancel_check=lambda: False)
    if entry == 'quality':
        auto.read_warehouse_quality(SimpleNamespace(database_path=path, scoring_database_path=STATIC,
                                                    config_dir=CONFIG), current, lambda: False)
    elif entry == 'first_rules':
        initial.generate_first_rules(**args)
    else:
        preview.build_preview(**args, asset_root=ROOT / 'data/role_catalog/game_ui')
    assert len(seen) == 1
