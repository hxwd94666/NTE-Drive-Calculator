# 验证智能规则的持久化、用户所有权、重新生成和实际计算边界。
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.domain.equipment.warehouse.intelligent_rules import generated_rules, merge_generated, policy_from_rules, preserve_origin
from src.domain.inventory.post_actions import merge_post_action_config, validate_post_action_config
from src.domain.equipment.warehouse.warehouse_recommendation import recommend
from tests.warehouse.test_warehouse_recommendation import item
from src.domain.equipment.eligibility.management_settings import MatchingScope, normalize_warehouse_config
from src.services.inventory.management.management_settings import ManagementSettingsService


def rules():
    return generated_rules({'grade': 'ACE'}, strong_set_name='失落光芒')


def test_regeneration_replaces_all_intelligent_rules_and_preserves_manual_rules(tmp_path):
    generated = rules()
    manual = {'enabled': True, 'name': '手写保留', 'item_type': 'tape', 'main_stats': ['生命值%']}
    edited = preserve_origin(generated[1], {**generated[1], 'name': '我改过', 'action': 'keep'})
    original = merge_post_action_config({'preserve_rules': [manual, generated[0], edited, *generated[2:]],
                                         'recommendation': {'rule_scheme_version': 3, 'characters': [1003]}})
    assert validate_post_action_config(original) is None
    from src.storage.sqlite.user_data_dao import UserDataDao
    with UserDataDao(tmp_path / 'user.sqlite3', account_id='test'):
        pass
    service = ManagementSettingsService(tmp_path / 'user.sqlite3', scope=MatchingScope.WAREHOUSE)
    expected, matching = service.load()
    original = normalize_warehouse_config(original)
    service.save(original, matching, expected_config=expected, expected_matching=matching)
    loaded = service.load()[0]
    assert loaded == original
    regenerated = merge_generated(loaded['preserve_rules'], rules())
    assert regenerated == [original['preserve_rules'][0], *rules()]
    assert regenerated[0] == original['preserve_rules'][0]
    assert regenerated[2]['name'] != '我改过'
    assert len(regenerated) == len(generated) + 1
    assert regenerated[-1]['smart_kind'] == 'redundant'


def test_disabled_modified_and_deleted_intelligent_rules_are_regenerated():
    generated = rules()
    removed = generated.pop(0)['intelligent']['key']
    generated[0]['enabled'] = False
    generated[1]['reserve'] = 9
    old = merge_post_action_config({'preserve_rules': generated, 'recommendation': {'deleted_keys': [removed]}})
    assert 'deleted_keys' not in old['recommendation']
    merged = merge_generated(old['preserve_rules'], rules())
    assert merged == rules()
    assert any(r['intelligent']['key'] == removed for r in merged)


def test_dynamic_rule_parameters_change_recommendations_and_disable_redundancy():
    group = [replace(item(str(i), score=10-i, suit='Other', stats={'攻击力': 1}), equipped=False) for i in range(4)]
    selected = [r for r in rules() if r.get('smart_kind') in {'current_tape', 'redundant'}]
    def calculate(rows):
        return recommend(group, selected_roles=['残虹'], demands={}, baseline_uids=frozenset(),
                         policy=policy_from_rules(rows))['rows']
    assert [r['decision'] for r in calculate(selected)].count('discard') == 2
    selected[0]['reserve'] = 3
    assert [r['decision'] for r in calculate(selected)].count('discard') == 1
    selected[1]['enabled'] = False
    assert not any(r['decision'] == 'discard' for r in calculate(selected))


def test_dynamic_rules_never_fall_through_to_ordinary_matching():
    from src.domain.inventory.post_actions import preserve_rule_matches_item
    for r in rules():
        if r.get('smart_kind'):
            assert not preserve_rule_matches_item(object(), r)
    duplicate = deepcopy(rules()[0])
    with pytest.raises(ValueError, match='只能启用一条'):
        policy_from_rules([duplicate, deepcopy(duplicate)])


def test_generated_conditions_match_original_future_combinations():
    from itertools import combinations, product
    from src.models.equipment import Tape
    from src.domain.inventory.post_actions import preserve_rule_matches_item
    names = {'Suit6': '失落光芒', 'Other': '普通套装', 'Sonic': '音速索尼克'}
    generated = generated_rules({'grade': 'SSS'}, strong_set_name=names['Suit6'], all_sets=list(names.values()))
    attributes = ['暴击率%', '暴击伤害%', '生命值%', '防御力', '攻击力%', '伤害增加%', '环合强度']
    groups = [*combinations(attributes, 3), *combinations(attributes, 4)]
    for main, suit, quality, keys in product(['生命值%', '防御力%', '治疗加成', '攻击力%', '暴击率%', '咒属性异能伤害增强%'], names, ['Gold', 'Blue'], groups):
        fact = item(main=main, suit=suit, stats=dict.fromkeys(keys, 1))
        tape = Tape(uid='synthetic', quality=quality, area=15, set_name=names[suit], main_stats=main, sub_stats=dict(fact.stats))
        # 历史已确认组合的独立真值表；生产代码只保留一套可编辑规则匹配。
        dual = {'暴击率%', '暴击伤害%'} <= set(keys)
        support = bool({'伤害增加%', '环合强度', '攻击力%'} & set(keys))
        scaling = bool({'生命值%', '生命值'} & set(keys)) if main == '生命值%' else bool({'防御力%', '防御力'} & set(keys))
        expected = dual and support and (suit == 'Suit6' or main in {'攻击力%', '暴击率%', '暴击伤害%'}
            or main in {'生命值%', '防御力%'} and scaling
            or main == '治疗加成' and bool({'生命值%', '生命值', '防御力%', '防御力'} & set(keys))
            or '伤害增加%' in keys and bool({'攻击力%', '环合强度'} & set(keys)))
        assert any(preserve_rule_matches_item(tape, r) for r in generated) == expected


def test_default_persisted_policy_matches_old_recommendation_decisions():
    import random
    from src.models.equipment import Tape, Drive
    from src.domain.inventory.post_actions import preserve_rule_matches_item
    from src.domain.equipment.warehouse.warehouse_recommendation import future_profiles
    rng = random.Random(32)
    generated = generated_rules({'grade': 'SSS'}, strong_set_name='失落光芒', all_sets=['失落光芒', '普通套装'])
    facts, protection = [], {}
    for index in range(100):
        stats = dict.fromkeys(rng.sample(['暴击率%', '暴击伤害%', '生命值%', '防御力', '攻击力%', '伤害增加%', '环合强度'], 4), 1)
        fact = item(str(index), main=rng.choice(['生命值%', '防御力%', '治疗加成', '攻击力%']), suit=rng.choice(['Suit6', 'Other']), stats=stats, score=rng.randint(1, 150))
        fact = replace(fact, scores=(*fact.scores, *((p['key'], rng.randint(1, 150)) for p in future_profiles())),
                       eligible_roles=() if index % 9 == 0 else fact.eligible_roles)
        if index % 3 == 0:
            fact = replace(fact, kind='drive', area=3, shape_id='H_3', main_stat='')
        facts.append(fact)
        tape = (Drive(uid=fact.uid, quality='Gold', area=3, shape_id='H_3', main_stats={'攻击力': 1, '生命值': 1}, sub_stats=stats) if fact.kind == 'drive' else
                Tape(uid=fact.uid, quality='Gold', area=15, set_name='失落光芒' if fact.suit_id == 'Suit6' else '普通套装', main_stats=fact.main_stat, sub_stats=stats))
        protection[fact.uid] = [(r['action'], r['name']) for r in generated if preserve_rule_matches_item(tape, r)]
    args = dict(selected_roles=['残虹'], demands={'残虹': {'H_3': 1.5}}, baseline_uids=frozenset({'0', '5'}))
    new = recommend(facts, **args, policy=policy_from_rules(generated), rule_protection=protection)
    # 移除旧入口前，固定种子混合装备在原 v5 算法上的冻结结果。
    locks = {0, 5, 10, 16, 17, 20, 22, 25, 56, 78}
    discards = {44, 50, 55, 61, 68, 74, 80, 89, 95}
    reviews = {9, 54}
    expected = ['lock' if i in locks else 'discard' if i in discards else 'review' if i in reviews else 'keep' for i in range(100)]
    # 用户追加四词条无数量上限保护：只允许把旧弃置/待定提升为保留，不改原有配装和锁定。
    for index, fact in enumerate(facts):
        keys = dict(fact.stats)
        premium = ({'暴击率%', '暴击伤害%', '伤害增加%'} <= keys.keys()
                   and {'攻击力%', '环合强度'} & keys.keys())
        if fact.kind == 'drive':
            premium = premium or ({'暴击率%', '暴击伤害%'} <= keys.keys() and (
                '伤害增加%' in keys and bool({'生命值%', '防御力%', '倾陷强度'} & keys.keys())
                or '攻击力%' in keys and bool({'环合强度', '倾陷强度'} & keys.keys())))
        if premium:
            if expected[index] in {'discard', 'review'}:
                expected[index] = 'keep'
    assert [r['decision'] for r in new['rows']] == expected


def test_rule_plans_are_account_local_and_cancel_does_not_persist(tmp_path):
    from src.storage.sqlite.user_data_dao import UserDataDao
    for name in ('one', 'two'):
        with UserDataDao(tmp_path / (name + '.sqlite3'), account_id=name):
            pass
    first, second = (tmp_path / (name + '.sqlite3') for name in ('one', 'two'))
    service = ManagementSettingsService(first, scope=MatchingScope.WAREHOUSE)
    config = normalize_warehouse_config({'preserve_rules': rules(), 'recommendation': {'rule_scheme_version': 3}})
    expected, matching = service.load()
    service.save(config, matching, expected_config=expected, expected_matching=matching)
    loaded = service.load()[0]
    assert loaded == config
    loaded['preserve_rules'][0]['enabled'] = False  # 未保存的编辑不影响账号库。
    assert service.load()[0] == config
    assert not ManagementSettingsService(second, scope=MatchingScope.WAREHOUSE).load()[0]['preserve_rules']


def test_stale_recommendation_clears_only_generated_pending_targets():
    from unittest.mock import Mock
    from src.features.inventory.warehouse.management.management_projection import invalidate_management_preview
    guard = Mock()
    guard.require_current.side_effect = ValueError('rules changed')
    owner = SimpleNamespace(recommendation_guard=guard, recommendation_targets={'generated': 'discarded'}, manual_targets={'manual':'locked'}, conflicts=[], score_label=Mock())
    host = SimpleNamespace(warehouse_read_controller=owner,
        _warehouse_pending_state_changes={'generated': 'discarded', 'manual': 'locked'},
        _warehouse_base_states={'generated': 'normal', 'manual': 'normal'},
        _warehouse_all_items=[{'uid': 'generated', 'discarded': True, 'locked': False}, {'uid': 'manual', 'discarded': False, 'locked': True}],
        _apply_warehouse_filters=Mock(), _update_warehouse_save_state=Mock())
    invalidate_management_preview(host)
    assert host._warehouse_pending_state_changes == {'manual': 'locked'}
    assert not host._warehouse_all_items[0]['discarded']
    assert host._warehouse_all_items[1]['locked']
    assert owner.recommendation_guard is None and not owner.recommendation_targets




def test_empty_saved_policy_does_not_restore_hidden_default_protection():
    value = replace(item(), eligible_roles=())
    result = recommend([value], selected_roles=[], demands={}, baseline_uids=frozenset(), policy={})
    assert result['rows'][0]['decision'] == 'review'
    assert not result['rows'][0]['future_guards']


def test_preview_rejects_unsaved_rules_before_running_solver(monkeypatch):
    from src.services.inventory.recommendation import warehouse_recommendation_preview as module
    saved = merge_post_action_config({'preserve_rules': rules(), 'recommendation': {'rule_scheme_version': 3}})
    monkeypatch.setattr(module, 'post_config', lambda _: saved)
    monkeypatch.setattr(module, 'file_identity', lambda _: ())
    monkeypatch.setattr(module, 'recommendation_account_identity', lambda _: 'same')
    monkeypatch.setattr(module, 'MatchingSettingsService', lambda _, **kwargs: SimpleNamespace(load=lambda: {}))
    monkeypatch.setattr(module, 'run_warehouse_trial', lambda **_: pytest.fail('未保存时不可计算'))
    edited = deepcopy(saved)
    edited['preserve_rules'][0]['enabled'] = False
    with pytest.raises(ValueError, match='未保存'):
        module.build_preview(database='unused', static_database='unused', asset_root='unused', config_dir='unused', config=edited,
                             matching={}, options={}, check_context=lambda: None, cancel_check=lambda: False)


def test_old_unordered_scheme_requires_regeneration_without_running_solver(monkeypatch):
    from src.services.inventory.recommendation import warehouse_recommendation_preview as module
    old = merge_post_action_config({'preserve_rules': rules(), 'recommendation': {'rule_scheme_version': 1}})
    monkeypatch.setattr(module, 'post_config', lambda _: old)
    monkeypatch.setattr(module, 'file_identity', lambda _: ())
    monkeypatch.setattr(module, 'recommendation_account_identity', lambda _: 'same')
    monkeypatch.setattr(module, 'MatchingSettingsService', lambda _, **kwargs: SimpleNamespace(load=lambda: {}))
    monkeypatch.setattr(module, 'run_warehouse_trial', lambda **_: pytest.fail('旧规则不可直接计算'))
    with pytest.raises(ValueError, match='重新生成'):
        module.build_preview(database='unused', static_database='unused', asset_root='unused', config_dir='unused', config=old,
                             matching={}, options={}, check_context=lambda: None, cancel_check=lambda: False)
    assert old['recommendation']['rule_scheme_version'] == 1
