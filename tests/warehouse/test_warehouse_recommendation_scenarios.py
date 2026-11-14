# 用合成库存验证稀缺装备、少角色和大量优秀装备的保护，以及公共内核整批移除复算。
from dataclasses import replace
from pathlib import Path

import pytest

from src.domain.equipment.warehouse.intelligent_rules import generated_rules, policy_from_rules
from src.domain.equipment.warehouse.warehouse_recommendation import WarehouseFact, recommend
from src.domain.inventory.post_actions import merge_post_action_config, preserve_rule_matches_item, validate_post_action_config
from src.models.equipment import Drive, Tape
from src.optimizer.allocation_kernel import AllocationKernel, AllocationKernelRequest
from src.optimizer.scoring import ScoringEngine
from src.services.inventory.recommendation.warehouse_recommendation import _plan_summary
from src.domain.equipment.warehouse.warehouse_rule_order import apply_ordered_rules
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao

ROOT = Path(__file__).resolve().parents[2]
CRIT = {'暴击率%': 10, '暴击伤害%': 20}
DRIVE_COMBINATIONS = [
    {**CRIT, '伤害增加%': 10, stat: value}
    for stat, value in [('攻击力%', 12.5), ('环合强度', 60), ('倾陷强度', 60), ('生命值%', 12.5), ('防御力%', 12.5)]
] + [{**CRIT, '攻击力%': 12.5, stat: 60} for stat in ('环合强度', '倾陷强度')]


def plan_rules(sets, shapes):
    result = generated_rules({'grade': 'SSS'}, strong_set_name='失落光芒', all_sets=sets, all_shapes=shapes)
    assert validate_post_action_config(merge_post_action_config({'preserve_rules': result})) is None
    return result


def protection(items, rules):
    return {i.uid: [(r['action'], r['name']) for r in rules if r.get('intelligent')
                   and preserve_rule_matches_item(i, r)] for i in items}


def fact(i, roles=('owned',)):
    return WarehouseFact(i.uid, i.item_type, getattr(i, 'suit_id', '') or '', i.shape_id,
                         i.main_stats if isinstance(i, Tape) else '', i.area, i.quality,
                         tuple(i.sub_stats.items()), tuple(i.role_scores.items()) or (('owned', 1),), roles)


def test_confirmed_tape_combinations_protect_every_retail_suit_and_cross_element():
    with StaticGameDataDao(ROOT / 'data/game_static.sqlite3') as db:
        sets = [r['name_zh'] for r in db.list_suits()]
    rules = plan_rules(sets, ['H_2', 'V_2', 'H_3'])
    combinations = [
        ('倾陷强度', {**CRIT, '伤害增加%': 10, '倾陷强度': 60}),
        ('环合强度', {**CRIT, '伤害增加%': 10, '环合强度': 60}),
        ('生命值%', {**CRIT, '伤害增加%': 10, '生命值%': 12.5}),
        ('防御力%', {**CRIT, '伤害增加%': 10, '防御力%': 12.5}),
    ] + [(main, {**CRIT, '伤害增加%': 10, stat: value})
         for main in ('暴击率%', '暴击伤害%', '光属性异能伤害增强%', '咒属性异能伤害增强%')
         for stat, value in [('攻击力%', 12.5), ('环合强度', 60)]]
    items = [Tape(uid=f'tape-{s}-{n}-{copy}', quality='Gold', area=15, set_name=suit,
                  main_stats=main, sub_stats=stats)
             for s, suit in enumerate(sets) for n, (main, stats) in enumerate(combinations) for copy in range(8)]
    result = recommend([fact(i) for i in items], selected_roles=['owned'], demands={}, baseline_uids=frozenset(),
                       policy=policy_from_rules(rules), rule_protection=protection(items, rules))
    assert len(result['rows']) == len(sets) * 12 * 8
    assert all(r['decision'] in {'keep', 'lock'} and r['future_guards'] for r in result['rows'])
    # 仅拥有一个角色甚至没有适配模型，也不能丢掉已确认的完整四词条组合。
    unknown = recommend([fact(i, ()) for i in items], selected_roles=[], demands={}, baseline_uids=frozenset(),
                        policy=policy_from_rules(rules), rule_protection=protection(items, rules))
    assert all(r['decision'] == 'keep' for r in unknown['rows'])


def test_many_seven_premium_drive_combinations_survive_even_with_single_candidate_quota():
    shapes = {'H_2': 2, 'V_2': 2, 'H_3': 3, 'H_4': 4}
    rules = plan_rules(['普通套装'], shapes)
    for r in rules:
        if r.get('smart_kind') == 'current_drive':
            r['reserve'] = 1
        elif r.get('smart_kind') in {'future', 'grade'}:
            r['enabled'] = False
    items = [Drive(uid=f'drive-{shape}-{n}-{copy}', quality='Gold', area=area, shape_id=shape,
                   main_stats={'攻击力': 42, '生命值': 560}, sub_stats=stats)
             for shape, area in shapes.items() for n, stats in enumerate(DRIVE_COMBINATIONS) for copy in range(25)]
    args = dict(selected_roles=['owned'], demands={'owned': dict.fromkeys(shapes, 1)}, baseline_uids=frozenset(),
                policy=policy_from_rules(rules))
    unprotected = recommend([fact(i) for i in items], **args)
    assert sum(r['decision'] == 'discard' for r in unprotected['rows']) > 600
    result = recommend([fact(i) for i in items], **args, rule_protection=protection(items, rules))
    assert len(result['rows']) == 700
    assert all(r['decision'] == 'keep' for r in result['rows'])
    # 明确排在前面的人工弃置规则按用户顺序决定，后续保护不再暗中覆盖。
    manual = merge_post_action_config({'preserve_rules': [{
        'name': '冲突弃置', 'item_type': 'drive', 'action': 'discard', 'sub_stats': ['暴击率%'],
        'sub_match': 1, 'shape_ids': list(shapes), 'quality_scope': 'all'}]})['preserve_rules']
    apply_ordered_rules(result, items, manual, matched_by_uid={i.uid: manual for i in items})
    assert all(r['decision'] == 'discard' for r in result['rows'])


@pytest.mark.parametrize('missing', ['暴击率%', '暴击伤害%', '伤害增加%', '攻击力%'])
def test_explicit_premium_rule_requires_four_real_positive_stats(missing):
    rules = [r for r in plan_rules(['普通套装'], ['H_3']) if r.get('intelligent', {}).get('key') == 'combo:premium:drive:攻击力%']
    stats = dict(DRIVE_COMBINATIONS[0])
    stats[missing] = 0
    value = Drive(uid='zero', quality='Gold', area=3, shape_id='H_3', main_stats={'攻击力': 1, '生命值': 1}, sub_stats=stats)
    assert not any(preserve_rule_matches_item(value, r) for r in rules)


def test_retention_budget_limits_only_extra_future_items_and_can_overflow():
    rules = plan_rules(['普通套装'], ['H_3'])
    policy = policy_from_rules(rules)
    policy.pop('grade')
    # 不命中明确四词条组合，副词条签名各异，原来每桶储备会放大总数。
    items = [Drive(uid=str(n), quality='Gold', area=3, shape_id='H_3', main_stats={'攻击力': 1, '生命值': 1},
                   sub_stats={'攻击力%': 1, '攻击力': n + 1, '暴击率%': 1},
                   role_scores={'owned': 1, 'future:attack': n + 1}) for n in range(30)]
    facts = [fact(i) for i in items]
    args = dict(selected_roles=['owned'], demands={'owned': {'H_3': 1}}, baseline_uids=frozenset(),
                policy=policy, rule_protection={str(n): [('keep', '优秀组合')] for n in range(5)})
    policy['future']['target_inventory_size'] = 8
    bounded = recommend(facts, **args)
    assert all(bounded['rows'][n]['decision'] != 'discard' for n in range(5))
    assert sum(r['decision'] != 'discard' for r in bounded['rows']) <= 8
    # 预算低于明确保护数量时允许超额，不通过强制弃置凑数量。
    policy['future']['target_inventory_size'] = 3
    overflow = recommend(facts, **args)
    assert overflow['reserve_budget']['protected_overflow'] > 0
    assert all(overflow['rows'][n]['decision'] != 'discard' for n in range(5))
    # 手写保护也占据预算；它的动作仍由独立规则仲裁，预算不夺取其所有权。
    manual = recommend(facts, **args, budget_protected_uids=frozenset({'29'}))
    assert manual['reserve_budget']['protected'] == overflow['reserve_budget']['protected'] + 1


@pytest.mark.parametrize('copies,owned', [(0, 1), (1, 1), (2, 1), (2, 2), (80, 1), (80, 2)])
def test_sparse_and_abundant_inventory_reallocate_with_public_scoring_kernel(copies, owned):
    # 一个最小合法合成蓝图使测试只覆盖分配/复算，不运行全图纸枚举或读取个人账号。
    weights = {'攻击力%': 1, '暴击率%': 1, '暴击伤害%': 1, '伤害增加%': 1, '环合强度': 0.5}
    roles = {name: {'character_id': n + 1, 'default_set': '普通套装', 'weights': weights,
                    'main_weights': {'攻击力%': 1}, 'board_matrix': [[1, 1, 1]],
                    'crit_source_known': True, 'non_equipment_crit_rate': 0.0, 'effective_fork_id': ''}
             for n, name in enumerate(('owned', 'other'))}
    selected = tuple(roles)[:owned]
    items = []
    for index in range(copies):
        for label, stats in [('premium', DRIVE_COMBINATIONS[0]), ('weak', {'攻击力%': 1})]:
            items.append(Tape(uid=f'{label}-t-{index}', quality='Gold', area=15, set_name='普通套装', suit_id='test-suit',
                              main_stats='攻击力%', main_value=37.5, sub_stats=stats))
            items.append(Drive(uid=f'{label}-d-{index}', quality='Gold', area=1, shape_id='X',
                               main_stats={'攻击力': 42, '生命值': 560}, sub_stats=stats))
    scorer = ScoringEngine(ROOT / 'config', roles_db=roles, static_database_path=ROOT / 'data/game_static.sqlite3')
    scorer.prepare_scores(items, assign_scores=True)
    request = AllocationKernelRequest(inventory=tuple(items), roles_db=roles,
        sets_db={'普通套装': {'suit_id': 'test-suit', 'shapes': []}}, shapes_db={},
        blueprints_db={r: [{'set_pieces': [], 'extra_pieces': ['X', 'X', 'X'], 'board': [], 'set_effect_mode': 'none'}] for r in selected},
        role_order=selected, strategy='role_priority', module_set_targets={}, set_effect_modes={},
        core_main_filters={}, core_set_targets={}, stat_priority_configs={}, property_limits={})
    before = _plan_summary(AllocationKernel(scorer).execute(request))
    baseline = frozenset(uid for p in before.values() if p['valid'] for uid in p['uids'])
    incomplete = any(not before.get(r, {}).get('valid') for r in selected)
    rules = plan_rules(['普通套装'], ['X'])
    result = recommend([fact(i, tuple(roles)) for i in items], selected_roles=selected,
        demands={r: {'X': 3} for r in selected}, baseline_uids=baseline, incomplete=incomplete,
        policy=policy_from_rules(rules), rule_protection=protection(items, rules))
    removed = {r['uid'] for r in result['rows'] if r['decision'] == 'discard'}
    assert not any(uid.startswith('premium') for uid in removed)
    if incomplete:
        assert not removed
    if copies == 80:
        assert removed, '丰富库存中仍应识别低质冗余，不能通过保留一切绕过检查'
    after = _plan_summary(AllocationKernel(scorer).execute(replace(request, inventory=tuple(i for i in items if i.uid not in removed))))
    for role, prior in before.items():
        if prior['valid']:
            assert after[role]['valid']
            assert after[role]['score'] + 1e-7 >= prior['score']
    all_used = [uid for row in after.values() if row['valid'] for uid in row['uids']]
    assert len(all_used) == len(set(all_used))
    assert not result['violations']
