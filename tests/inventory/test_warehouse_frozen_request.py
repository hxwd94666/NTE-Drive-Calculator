# 验证冻结后不再读取账号，偏好约束不丢失，输入缺项和当前域关联损坏明确失败。
from concurrent.futures import CancelledError
from contextlib import closing
from dataclasses import replace
import sqlite3
from types import SimpleNamespace

import pytest

from tests.warehouse.test_warehouse_input_snapshot import account as account
from tests.warehouse.test_warehouse_input_snapshot import freeze
from tests.warehouse.test_warehouse_input_snapshot import STATIC
from tests.warehouse.test_warehouse_input_snapshot import CONFIG
from src.services.inventory.recommendation import warehouse_calculation_inputs as module
from src.services.inventory.recommendation.warehouse_recommendation import run_warehouse_trial
from src.services.inventory.recommendation import warehouse_recommendation_profiles as profiles
from src.storage.sqlite.user_data_dao import UserDataDao


def test_frozen_scoring_never_reopens_account_even_through_identification(account, monkeypatch):
    path, _, _, _ = account
    inputs = freeze(path)
    def reject(*args, **kwargs):
        pytest.fail('冻结后不再打开账号 DAO')
    monkeypatch.setattr(UserDataDao, '__init__', reject)
    scoring, context = module.warehouse_scoring(inputs, STATIC, CONFIG, lambda: False)
    assert scoring.user_database_path is None
    assert scoring.roles_db == inputs.values()['roles']
    assert not context.errors


def test_actual_role_catalog_and_nested_detail_borrow_the_same_account_owner(account, monkeypatch):
    from src.services.allocation.execution.legacy_allocation_static_catalog import build_legacy_allocation_static_catalog
    from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
    path, _, _, _ = account
    with UserDataDao.read_only(path) as user, user.read_consistent_state(), static_catalog_read_unit(STATIC) as unit:
        def reject(*args, **kwargs):
            pytest.fail('借用读取单元时不新开账号连接')
        monkeypatch.setattr(UserDataDao, '__init__', reject)
        catalog = build_legacy_allocation_static_catalog(config_dir=CONFIG, user_database_path=path,
            static_database_path=STATIC, static_read_unit=unit, user_read_unit=user)
        assert catalog.roles_db


def test_profile_protection_requires_explicit_complete_materialization(account):
    path, _, _, _ = account
    inputs = freeze(path)
    assert inputs.profiles_frozen is False
    with pytest.raises(ValueError, match='校验范围'):
        run_warehouse_trial(inputs=inputs, static_database=STATIC, config_dir=CONFIG)


def test_management_scoring_has_no_live_account_warehouse_bypass(account):
    from src.domain.equipment.eligibility.management_settings import MatchingScope
    from src.services.identification.scoring_scope import prepare_management_scoring
    path, _, _, _ = account
    inputs = freeze(path)
    for scope, frozen in ((MatchingScope.WAREHOUSE, None), (MatchingScope.SCAN, inputs)):
        with pytest.raises(ValueError, match='身份不一致'):
            prepare_management_scoring(inputs.values()['config'], [], config_dir=CONFIG, database=path,
                                       static_database=STATIC, scope=scope, frozen_inputs=frozen)


def test_exact_latest_profile_constraints_are_retained_and_detached(account):
    path, _, current, _ = account
    character = dict(character_id=1003, property_limits={'CritBase': {'minimum': 0.01, 'maximum': 1.0}},
                     substat_blacklist=['AtkAdd'], blacklist_zero_weight=True, crit_threshold=0.5)
    with UserDataDao(path) as user:
        profile = user.create_optimization_profile('constraints', allocation_strategy='role_priority', characters=[character])
        user.create_optimization_profile_version(profile['profile_id'], allocation_strategy='role_priority',
                                                characters=[{**character, 'crit_threshold': 0.6}])
    inputs = freeze(path, include_profiles=True)
    assert inputs.profiles_frozen
    assert len(inputs.profile_contexts) == 1
    context = inputs.profile_contexts[0]
    assert context.profile_version == 2
    assert context.snapshot.snapshot_id == current
    assert context.roles[0].crit_threshold == 0.6
    assert context.roles[0].substat_blacklist == ('AtkAdd',)
    assert context.roles[0].blacklist_zero_weight
    assert context.roles[0].property_limits
    with UserDataDao(path) as user:
        user.deactivate_optimization_profile(profile['profile_id'])
    assert inputs.profile_contexts[0] == context
    assert freeze(path, include_profiles=True).profile_contexts == ()


def test_failed_profile_preparation_is_still_incomplete_not_silently_skipped(account, monkeypatch):
    path, _, _, _ = account
    with UserDataDao(path) as user:
        user.create_optimization_profile('bad-context', allocation_strategy='role_priority', characters=[{'character_id': 1003}])
    def failed(*args, **kwargs):
        raise ValueError('fixture')
    monkeypatch.setattr(profiles, 'build_allocation_context', failed)
    inputs = freeze(path, include_profiles=True)
    assert inputs.profiles_frozen and inputs.profile_issues
    witnesses, issues = profiles.freeze_profile_witnesses(inputs, lambda: None, lambda: False, solve=failed)
    assert witnesses == []
    assert issues == list(inputs.profile_issues)
    monkeypatch.setattr(profiles, 'build_allocation_context', lambda *a, **k: (_ for _ in ()).throw(CancelledError()))
    with pytest.raises(CancelledError):
        freeze(path, include_profiles=True)


@pytest.mark.parametrize('table', ['inventory_item_stat', 'inventory_item_runtime_state', 'role_loadout_slot',
                                 'loadout_plan', 'optimization_preference_property_limit'])
def test_current_domain_broken_references_reject_entire_input(account, table):
    path, _, current, _ = account
    with closing(sqlite3.connect(path)) as db, db:
        if table == 'role_loadout_slot':
            db.execute('UPDATE role_loadout_slot SET current_plan_id=999999')
        elif table == 'loadout_plan':
            db.execute('UPDATE loadout_plan SET source_snapshot_id=999999 WHERE allocation_locked=1')
        elif table == 'optimization_preference_property_limit':
            db.execute("INSERT INTO optimization_preference_profile(profile_id,name,is_active,created_at_utc,updated_at_utc) "
                       "VALUES(100,'broken',1,'a','a')")
            cursor = db.execute('INSERT INTO optimization_preference_version(profile_id,version_number,allocation_strategy,created_at_utc) '
                                "VALUES(100,1,'role_priority','a')")
            db.execute('INSERT INTO optimization_preference_property_limit(profile_version_id,character_id,property_id,minimum_value) '
                       "VALUES(?,99999,'AtkAdd',0)", (cursor.lastrowid,))
        elif table == 'inventory_item_stat':
            db.execute('UPDATE inventory_item_stat SET uid_serial=999999 WHERE snapshot_id=?', (current,))
        else:
            db.execute('INSERT INTO inventory_item_runtime_state(snapshot_id,uid_serial,uid_slot,locked,discarded,equipped,updated_at_utc) '
                       "VALUES(?,999999,999999,0,0,0,'test')", (current,))
    with pytest.raises(ValueError, match='关联数据'):
        freeze(path)


def test_partial_current_inventory_and_mismatched_static_identity_are_rejected(account):
    path, _, current, _ = account
    inputs = freeze(path)
    with pytest.raises(ValueError, match='资料或规则'):
        module.warehouse_orchestrator(replace(inputs, static_sha256='0' * 64), STATIC, CONFIG, lambda: False)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('UPDATE inventory_snapshot SET complete=0 WHERE snapshot_id=?', (current,))
    with pytest.raises(ValueError, match='完整库存'):
        freeze(path)


def test_frozen_profile_removal_keeps_original_context_and_constraints(account):
    path, _, _, _ = account
    with UserDataDao(path) as user:
        user.create_optimization_profile('constraints', allocation_strategy='role_priority',
            characters=[dict(character_id=1003, property_limits={'AtkAdd': {'minimum': 0.0}}, crit_threshold=0.0)])
    inputs = freeze(path, include_profiles=True)
    prior = inputs.profile_contexts[0]
    seen = []
    def run(context, cancelled, **kwargs):
        seen.append(context)
        return SimpleNamespace(role_keys=(), plans={})
    profiles.freeze_profile_witnesses(inputs, lambda: None, lambda: False, solve=run,
                                      excluded_uids=frozenset({'nte-module-23-13'}))
    assert seen[0].candidates == ()
    assert seen[0].roles == prior.roles
    assert inputs.profile_contexts[0].candidates == prior.candidates and prior.candidates


def test_unknown_runtime_state_is_preserved_in_frozen_decisions(account, monkeypatch):
    from src.domain.equipment.warehouse.warehouse_decisions import DecisionStatus
    from src.services.inventory.recommendation.warehouse_predecessors import evaluate_predecessors
    path, _, _, _ = account
    original = UserDataDao.list_inventory_items_with_runtime_state
    def unknown(self, *args, **kwargs):
        return [{**row, 'locked': None, 'discarded': None, 'equipped': None} for row in original(self, *args, **kwargs)]
    monkeypatch.setattr(UserDataDao, 'list_inventory_items_with_runtime_state', unknown)
    inputs = freeze(path)
    assert inputs.values()['originals'][0]['locked'] is None
    batch = evaluate_predecessors(inputs, STATIC, CONFIG, inputs.values()['config'])
    decision = next(iter(batch.decisions.values()))
    assert decision.status == DecisionStatus.UNKNOWN
    assert decision.target is None
