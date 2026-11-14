# 验证未来储备先检查套装主属性用途，并保留用户既有弃置决定及明确规则的优先级。
from dataclasses import replace

import pytest

from src.domain.equipment.warehouse.intelligent_rules import automatic_main_supported, generated_rules, policy_from_rules, preserve_origin
from src.domain.equipment.warehouse.warehouse_recommendation import future_eligible, future_profiles, recommend
from src.domain.inventory.post_actions import preserve_rule_matches_item
from src.models.equipment import Tape
from src.domain.equipment.warehouse.warehouse_rule_order import apply_ordered_rules
from src.services.inventory.recommendation.warehouse_recommendation_preview import preview_targets
from tests.warehouse.test_warehouse_recommendation import item


def attack():
    return future_profiles()[0]


def test_dark_suit_nature_main_has_no_speculative_use_but_matching_element_does():
    value = replace(item(suit='Suit1', main='灵属性异能伤害增强%',
                         stats={'伤害增加%': 10, '攻击力': 80, '防御力': 80, '暴击率%': 10}), eligible_roles=())
    assert not future_eligible(value, attack())
    assert future_eligible(replace(value, main_stat='暗属性异能伤害增强%'), attack())
    policy = policy_from_rules(generated_rules({}, strong_set_name='失落光芒'))
    result = recommend([value], selected_roles=[], demands={}, baseline_uids=frozenset(), policy=policy)
    assert result['rows'][0]['decision'] == 'review'
    assert not result['future_buckets']


def test_confirmed_cross_suit_character_still_provides_use_evidence():
    value = item(suit='Suit6', main='咒属性异能伤害增强%',
                 stats={'攻击力%': 12.5, '伤害增加%': 10, '暴击率%': 10})
    assert future_eligible(value, attack())
    assert not future_eligible(replace(value, eligible_roles=()), attack())


def test_healing_hypothesis_requires_supporting_suit_or_current_character():
    profile = next(p for p in future_profiles() if p['key'] == 'future:health_heal')
    value = replace(item(suit='Suit1', main='治疗加成',
                         stats={'生命值%': 12.5, '暴击率%': 10, '暴击伤害%': 20}), eligible_roles=())
    assert not future_eligible(value, profile)
    assert future_eligible(replace(value, suit_id='Suit9'), profile)
    assert future_eligible(replace(value, eligible_roles=('有明确适配的角色',)), profile)


@pytest.mark.parametrize('kind', ['tape', 'drive'])
def test_future_hypotheses_do_not_reverse_prior_discard_but_explicit_protection_does(kind):
    value = replace(item(suit='Suit1', main='暗属性异能伤害增强%',
                         stats={'攻击力%': 12.5, '暴击率%': 10, '攻击力': 80}),
                    kind=kind, discarded=True, eligible_roles=())
    assert not future_eligible(value, attack())
    policy = policy_from_rules(generated_rules({}, strong_set_name='失落光芒'))
    args = dict(selected_roles=[], demands={}, baseline_uids=frozenset(), policy=policy)
    assert recommend([value], **args)['rows'][0]['decision'] == 'review'
    assert recommend([value], **args, rule_protection={value.uid: [('keep', '用户确认组合')]})['rows'][0]['decision'] == 'keep'
    assert recommend([replace(value, equipped=True)], **args)['rows'][0]['decision'] == 'keep'


def evaluate_automatic(value, rules):
    tape = Tape(uid=value.uid, quality='Gold', area=15, set_name=value.suit_id,
                suit_id=value.suit_id, main_stats=value.main_stat, sub_stats=dict(value.stats))
    matched = [r for r in rules if preserve_rule_matches_item(tape, r) and automatic_main_supported(r, value)]
    result = recommend([value], selected_roles=[], demands={}, baseline_uids=frozenset(),
        policy=policy_from_rules(rules), rule_protection={value.uid: [(r['action'], r['name']) for r in matched]})
    apply_ordered_rules(result, [tape], rules, matched_by_uid={value.uid: matched},
                        protected_uids=frozenset({value.uid}) if value.locked or value.equipped else frozenset())
    result['rows'][0].update(original_locked=value.locked, original_discarded=value.discarded,
                            original_equipped=value.equipped, state_known=True)
    return result


def test_universal_link_stagger_health_defense_combinations_across_suits_and_states():
    from itertools import product
    suits = [f'Suit{n}' for n in range(1, 13)]
    rules = generated_rules({}, strong_set_name='Suit6', all_sets=suits)
    for suit, main, locked, discarded, equipped in product(
            suits, ['环合强度', '倾陷强度', '生命值%', '防御力%'], [False, True], [False, True], [False, True]):
        value = replace(item(suit=suit, main=main,
            stats={'暴击率%': 10, '暴击伤害%': 20, '伤害增加%': 10, main: 12.5}),
            eligible_roles=(), locked=locked, discarded=discarded, equipped=equipped)
        result = evaluate_automatic(value, rules)
        assert result['rows'][0]['decision'] == 'keep'
        assert preview_targets(result) == ({value.uid: 'normal'} if discarded and not locked else {})


def test_unknown_cross_element_cannot_bypass_gate_through_automatic_premium_rules():
    rules = generated_rules({}, strong_set_name='Suit6', all_sets=['Suit1', 'Suit6'])
    value = replace(item(suit='Suit1', main='灵属性异能伤害增强%',
        stats={'暴击率%': 10, '暴击伤害%': 20, '伤害增加%': 10, '攻击力%': 12.5}),
        eligible_roles=(), discarded=True)
    result = evaluate_automatic(value, rules)
    assert result['rows'][0]['decision'] == 'review'
    assert preview_targets(result) == {}
    # 同元素、当前角色证明及明确光套例外均可保留；编辑过的规则继续归用户所有。
    for good in (replace(value, main_stat='暗属性异能伤害增强%'),
                 replace(value, eligible_roles=('明确适配角色',)),
                 replace(value, suit_id='Suit6', main_stat='咒属性异能伤害增强%')):
        assert evaluate_automatic(good, rules)['rows'][0]['decision'] == 'keep'
    premium = next(r for r in rules if r.get('intelligent', {}).get('key') == 'combo:premium:tape:攻击力%')
    edited = preserve_origin(premium, {**premium, 'name': '我确认保留'})
    assert evaluate_automatic(value, [edited])['rows'][0]['decision'] == 'keep'
