# 验证三策略旧版同值迁移、单策略修订、草稿与八槽隔离、晚导入及失败后原库重试。
from copy import deepcopy
from contextlib import closing
import json
import sqlite3

import pytest

from src.domain.rewind.strategy_preferences import STRATEGIES, UI_KEY, initial_profiles, strategy_key
from src.services.rewind.recommendation import RewindShapeRecommendationService, RewindShapeAnalysis
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.rewind.test_rewind_slot_preferences import create_account

SHAPE = 'EquipmentGeometry_Hen2'


def service(path):
    return RewindShapeRecommendationService(user_database_path=path, static_database_path=path.with_suffix('.static'))


def legacy_account(path, legacy=None, dual=None):
    with UserDataDao(path, account_id='synthetic'):
        pass
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM schema_migration WHERE version=53')
        db.execute("DELETE FROM application_setting_copy WHERE setting_key LIKE 'rewind_strategy_%'")
        for key, value in [('rewind_recommendation', legacy or {}), ('rewind_dual_target', dual or {})]:
            db.execute('INSERT INTO application_setting_copy VALUES (?,?,?)', (key, json.dumps(value), 'fixture'))


def test_v52_upgrade_copies_identical_values_once_and_keeps_consistent_backup(tmp_path):
    path = tmp_path / 'user.sqlite3'
    old = dict(strategy='focused', target_character_ids=[1004], main_character_ids=[1005],
               target_grade='A', saved_rewind_shape_ids=[SHAPE] * 8, rewind_qualities=['purple'])
    legacy_account(path, old)
    with UserDataDao(path) as dao:
        values = dao.list_application_setting_copies()
        profiles = [values[strategy_key(k)] for k in STRATEGIES]
        assert profiles[0] == profiles[1] == profiles[2]
        assert values[UI_KEY]['strategy'] == 'focused'
        assert all(p['saved_plan']['saved_rewind_shape_ids'] == [SHAPE] * 8 for p in profiles)
        assert not {'rewind_recommendation', 'rewind_dual_target'} & values.keys()
    backups = list((tmp_path / '.migration-backups').glob('user-data-v52-*'))
    assert len(backups) == 1
    with closing(sqlite3.connect('file:' + backups[0].as_posix() + '?mode=ro&immutable=1', uri=True)) as db:
        assert db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        assert json.loads(db.execute("SELECT value_json FROM application_setting_copy WHERE setting_key='rewind_recommendation'").fetchone()[0]) == old
    current = service(path)
    balanced = current.load_preferences('balanced')
    current.save_preferences({**balanced, 'target_grade': 'SS'})
    with UserDataDao(path) as dao:
        again = dao.list_application_setting_copies()
        assert again[strategy_key('focused')] == profiles[1]
        assert again[strategy_key('dual_target')] == profiles[2]
    assert current.load_preferences('balanced')['target_grade'] == 'SS'
    assert len(list((tmp_path / '.migration-backups').glob('user-data-v52-*'))) == 1


def test_initial_values_are_deep_copies_and_original_role_lists_survive_overlap():
    old = dict(target_character_ids=[1, 2], main_character_ids=[2, 3], selected_slots={'1': 11, '2': 22, '3': 33})
    profiles = initial_profiles(old)
    profiles['balanced']['draft']['target_character_ids'].append(9)
    assert profiles['focused'] == profiles['dual_target']
    assert profiles['focused']['draft']['target_character_ids'] == [1, 2]
    assert profiles['focused']['draft']['groups']['improve']['slots'] == {'1': 11}
    assert profiles['focused']['draft']['groups']['push']['slots'] == {'2': 22, '3': 33}
    assert old['target_character_ids'] == [1, 2]


def test_draft_changes_preserve_saved_plan_and_other_strategy_and_account(tmp_path):
    one, two = tmp_path / 'one.sqlite3', tmp_path / 'two.sqlite3'
    for path in (one, two):
        with UserDataDao(path, account_id=path.stem):
            pass
    first = service(one)
    draft = first.load_preferences('balanced')
    saved = first.save_preferences({**draft, 'saved_rewind_shape_ids': [SHAPE] * 8}, save_plan=True)
    initial_plan = deepcopy(saved['saved_rewind_shape_ids'])
    first.save_preferences({**saved, 'target_grade': 'ACE', 'saved_rewind_shape_ids': ['ignored']})
    assert first.load_preferences('balanced')['saved_rewind_shape_ids'] == initial_plan
    assert 'target_grade' not in first.load_preferences('focused')
    assert 'saved_rewind_shape_ids' not in service(two).load_preferences('balanced')
    first.select_strategy('dual_target')
    assert first.load_preferences()['strategy'] == 'dual_target'
    assert service(two).load_preferences()['strategy'] == 'balanced'


def test_stale_revision_and_cancellation_do_not_overwrite_the_saved_state(tmp_path):
    path = tmp_path / 'one.sqlite3'
    with UserDataDao(path, account_id='one'):
        pass
    first = service(path)
    stale = first.load_preferences('focused')
    good = first.save_preferences({**stale, 'target_grade': 'A'})
    with pytest.raises(ValueError, match='conflict'):
        first.save_preferences({**stale, 'target_grade': 'B'})
    calls = []
    def cancel():
        calls.append(1)
        if len(calls) >= 3:
            raise InterruptedError('fixture cancellation after write')
    with pytest.raises(InterruptedError):
        first.save_preferences({**good, 'target_grade': 'SS'}, checkpoint=cancel)
    assert first.load_preferences('focused') == good


def test_upgrade_failure_keeps_schema_legacy_values_and_can_retry(tmp_path, monkeypatch):
    path = tmp_path / 'one.sqlite3'
    old = dict(target_grade='SS', saved_rewind_shape_ids=[SHAPE] * 8)
    legacy_account(path, old)
    original = UserDataDao._execute_migration_script
    def fail(connection, script):
        raise RuntimeError('fixture migration failure')
    monkeypatch.setattr(UserDataDao, '_execute_migration_script', staticmethod(fail))
    with pytest.raises(RuntimeError):
        UserDataDao(path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT MAX(version) FROM schema_migration').fetchone()[0] == 52
        assert db.execute("SELECT COUNT(*) FROM application_setting_copy WHERE setting_key LIKE 'rewind_strategy_%'").fetchone()[0] == 0
    monkeypatch.setattr(UserDataDao, '_execute_migration_script', staticmethod(original))
    assert service(path).load_preferences('dual_target')['target_grade'] == 'SS'


def test_old_json_import_after_new_empty_schema_seeds_once_without_questions(tmp_path):
    path = tmp_path / 'one.sqlite3'
    old = dict(target_grade='SSS', target_character_ids=[1004], saved_rewind_shape_ids=[SHAPE] * 8)
    with UserDataDao(path, account_id='one') as dao:
        dao.import_legacy_application_settings({'rewind_recommendation': old})
        values = dao.list_application_setting_copies()
        assert values[strategy_key('balanced')] == values[strategy_key('focused')] == values[strategy_key('dual_target')]
        assert 'rewind_recommendation' not in values
    first = service(path)
    initial = first.load_preferences('focused')
    first.save_preferences({**initial, 'target_grade': 'D'})
    with UserDataDao(path) as dao:
        dao.import_legacy_application_settings({'rewind_recommendation': {'target_grade': 'ACE'}})
        assert 'rewind_recommendation' not in dao.list_application_setting_copies()
    assert first.load_preferences('focused')['target_grade'] == 'D'


def test_generated_plan_save_overwrites_after_inventory_update_but_rejects_static_change(tmp_path):
    path = tmp_path / 'one.sqlite3'
    ref, _ = create_account(path, 'one')
    first = service(path)
    first.static_identity = lambda: ('fixture', 1, 1)
    with UserDataDao(path) as dao:
        source = dao.get_loadout_slot(ref.slot_id)['current_plan']['source_snapshot_id']
        snapshot = dao.current_inventory_snapshot_id()
    analysis = RewindShapeAnalysis(snapshot, 'fixture', 1, 8, (), strategy='balanced',
        selected_slots=(ref,), static_identity=first.static_identity(), selected_source_snapshots=((ref.slot_id, source),))
    value = dict(first.load_preferences('balanced'), saved_rewind_shape_ids=[SHAPE] * 8)
    with UserDataDao(path) as dao:
        # 生成后背包已同步出新快照：保存照常覆盖，冻结来源仍指向生成时快照。
        newer = dao.import_inventory_snapshot({"method": "event.inventory.snapshot", "params": {
            "complete": True, "generation": 1, "sequence": 2, "observed_at_unix_ms": 1_784_308_900_000,
            "item_count": 0, "items": []}})
    assert newer != snapshot
    saved = first.save_preferences(value, save_plan=True, analysis=analysis)
    assert saved['saved_guard']['snapshot_id'] == snapshot
    assert saved['saved_inputs']['slots'][0]['plan_id'] == ref.plan_id
    resaved = first.save_preferences(dict(saved, saved_rewind_shape_ids=['EquipmentGeometry_Hen4'] * 8), save_plan=True)
    assert resaved['saved_rewind_shape_ids'] == ['EquipmentGeometry_Hen4'] * 8
    assert resaved['saved_guard'] == saved['saved_guard'] and resaved['plan_origin'] == 'generated'
    saved = resaved
    first.static_identity = lambda: ('changed', 1, 1)
    with pytest.raises(ValueError):
        first.save_preferences({**saved}, save_plan=True, analysis=analysis)
    with pytest.raises(ValueError):
        first.save_preferences({**saved}, save_plan=True)  # 重开再次保存也不能降级冻结来源。
    assert first.load_preferences('balanced') == saved


@pytest.mark.parametrize('invalid', [[], False, 0, ''])
def test_malformed_legacy_does_not_silently_become_empty(invalid):
    with pytest.raises(ValueError, match='invalid_rewind_legacy'):
        initial_profiles(invalid)


def test_first_legacy_import_does_not_override_already_edited_profiles(tmp_path):
    path = tmp_path / 'one.sqlite3'
    with UserDataDao(path, account_id='one'):
        pass
    first = service(path)
    saved = first.save_preferences({**first.load_preferences('focused'), 'target_grade': 'D'})
    with UserDataDao(path) as dao:
        dao.import_legacy_application_settings({'rewind_recommendation': {'target_grade': 'ACE'}})
        values = dao.list_application_setting_copies()
        assert 'rewind_recommendation' not in values
    assert first.load_preferences('focused') == saved
    assert 'target_grade' not in first.load_preferences('balanced')


@pytest.mark.parametrize('limits', [{'gold': 0}, {'gold': -2}, {'gold': True}, {'red': 1}, {'gold': 1.5}, [3], {'gold': 10000}])
def test_ten_limits_are_saved_per_strategy_and_invalid_values_are_rejected(tmp_path, limits):
    path = tmp_path / 'user.sqlite3'
    legacy_account(path)
    current = service(path)
    balanced = current.load_preferences('balanced')
    current.save_preferences({**balanced, 'rewind_ten_limits': {'blue': 2, 'gold': 5}})
    assert current.load_preferences('balanced')['rewind_ten_limits'] == {'blue': 2, 'gold': 5}
    assert 'rewind_ten_limits' not in current.load_preferences('focused')
    saved = current.load_preferences('balanced')
    with pytest.raises(ValueError, match='invalid_rewind_profile'):
        current.save_preferences({**saved, 'rewind_ten_limits': limits})
    assert current.load_preferences('balanced')['rewind_ten_limits'] == {'blue': 2, 'gold': 5}
