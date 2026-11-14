# 验证数量目标下的同类多样性取舍不动原锁、配装见证和明确保留规则。
from dataclasses import replace
from copy import deepcopy
from types import SimpleNamespace
import pytest

from src.domain.equipment.warehouse.warehouse_recommendation import WarehouseFact, recommend
from src.domain.equipment.warehouse.warehouse_diversity import diversity_features


def fact(uid, critical, score, *, kind='drive', **kwargs):
    return WarehouseFact(uid=uid, kind=kind, suit_id='Suit1', shape_id='H_3', main_stat='暴击伤害%',
        area=3, quality='Gold', stats=((critical, 8.0), ('攻击力%', 5.0)),
        scores=(('role', score),), eligible_roles=('role',), **kwargs)


def run(values, target=2, grade=None, **kwargs):
    return recommend(values, selected_roles=('role',), demands={'role': {'H_3': 1}},
        baseline_uids=kwargs.pop('baseline_uids', frozenset()), target_inventory_size=target,
        policy={'current_drive': {'reserve': 3, 'action': 'keep'},
                'current_tape': {'reserve': 4, 'action': 'keep'},
                **({'grade': {'grade': grade}} if grade else {}), 'redundant': {'action': 'discard'}}, **kwargs)


def decisions(result):
    assert not result['violations']
    return {r['uid']: r['decision'] for r in result['rows']}


def test_weak_duplicate_is_removed_but_other_crit_direction_survives():
    for kind in ('drive', 'tape'):
        rows = [fact('better', '暴击伤害%', 20, kind=kind), fact('duplicate', '暴击伤害%', 19, kind=kind),
                fact('different', '暴击率%', 18, kind=kind)]
        result = run(rows)
        assert decisions(result) == {'better': 'keep', 'duplicate': 'discard', 'different': 'keep'}
        assert result['diversity_budget']['diversity_minimum'] == 2
        assert result['rows'][1]['similar_reserve_trimmed']


def test_locked_equipped_baseline_and_static_rules_are_never_trimmed():
    rows = [fact('better', '暴击伤害%', 20), fact('duplicate', '暴击伤害%', 19), fact('different', '暴击率%', 18)]
    for key in ('locked', 'equipped'):
        protected = [rows[0], replace(rows[1], **{key: True}), rows[2]]
        assert decisions(run(protected))['duplicate'] != 'discard'
    assert decisions(run(rows, baseline_uids=frozenset({'duplicate'})))['duplicate'] != 'discard'
    result = run(rows, budget_protected_uids=frozenset({'duplicate'}),
                 rule_protection={'duplicate': [('keep', '完美词条保留')]})
    assert decisions(result)['duplicate'] != 'discard'
    assert result['reserve_budget']['protected_overflow'] == 1


def test_budget_off_or_incomplete_keeps_existing_automatic_reserves():
    rows = [fact('a', '暴击伤害%', 20), fact('b', '暴击伤害%', 19)]
    assert decisions(run(rows, target=0)) == {'a': 'keep', 'b': 'keep'}
    assert decisions(run(rows, target=1, incomplete=True)) == {'a': 'keep', 'b': 'keep'}


def test_missing_direction_is_not_invented_from_main_stat():
    damage = fact('a', '暴击伤害%', 20, kind='tape')
    rate = fact('b', '暴击率%', 20, kind='tape')
    assert diversity_features(damage) != diversity_features(rate)


def test_already_discarded_unknown_item_does_not_consume_retained_budget():
    rows = [fact('keep', '暴击伤害%', 20), replace(fact('old', '暴击率%', 0),
            discarded=True, eligible_roles=(), scores=())]
    result = run(rows, target=1)
    assert result['reserve_budget']['protected'] == 1
    assert decisions(result)['old'] == 'review'


@pytest.mark.parametrize('grade, threshold', [('SS',18),('SSS',21),('ACE',24)])
def test_selected_grade_is_hard_protection_even_for_duplicate_and_over_budget(grade, threshold):
    rows=[fact('best','暴击伤害%',29),fact('above','暴击伤害%',threshold+1),
          fact('at','暴击伤害%',threshold),fact('below','暴击伤害%',threshold-1)]
    result=run(rows,target=1,grade=grade)
    assert decisions(result)=={'best':'keep','above':'keep','at':'keep','below':'discard'}
    assert result['reserve_budget']['protected_overflow']==2
    for row in result['rows'][:3]:
        assert row['dynamic_rule_matches']['grade']=='keep'
        assert not row['similar_reserve_trimmed']


def test_current_use_lock_cannot_be_trimmed_when_grade_is_disabled():
    rows=[replace(fact('a','暴击伤害%',30),scores=(('a',30),('b',10)),eligible_roles=('a','b')),
          replace(fact('b','暴击伤害%',28),scores=(('a',20),('b',28)),eligible_roles=('a','b'))]
    result=recommend(rows,selected_roles=('a','b'),demands={'a':{'H_3':1},'b':{'H_3':1}},
        baseline_uids=frozenset(),target_inventory_size=1,
        policy={'current_drive':{'reserve':1,'action':'lock'},'redundant':{'action':'discard'}})
    assert decisions(result)=={'a':'lock','b':'lock'}
    assert result['reserve_budget']['protected_overflow']==1


def test_budget_protection_does_not_reorder_explicit_user_rules():
    from src.domain.equipment.warehouse.warehouse_rule_order import apply_ordered_rules
    result=run([fact('a','暴击伤害%',24)],target=1,grade='SS')
    grade={'smart_kind':'grade','action':'keep','name':'评级保留'}
    discard={'action':'discard','name':'用户明确弃置'}
    for rules,expected in (([grade,discard],'keep'),([discard,grade],'discard')):
        candidate=deepcopy(result)
        apply_ordered_rules(candidate,[SimpleNamespace(uid='a')],rules,matched_by_uid={'a':[discard]})
        assert candidate['rows'][0]['decision']==expected
