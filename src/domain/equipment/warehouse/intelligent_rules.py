# 生成可持久化的智能规则，重新生成整体替换智能来源，保留人工来源。
from copy import deepcopy
import hashlib
import json

from src.domain.localization.catalog import localized_text
from src.domain.equipment.scoring.grade_limits import GRADE_MIN_RATIOS

RULE_SCHEME_VERSION = 3


KINDS = {
    'cross_suit': ('残虹光套适配', '本次推荐允许残虹使用光套，按正式角色与套装 ID 加入适配；关闭后完全沿用已保存套装匹配。'),
    'baseline': ('全角色配装保护', '全部可计算角色参与，按完整合法蓝图求解；原锁和可行配装始终保护，关闭本规则只取消新增锁定。'),
    'current_tape': ('全角色空幕用途', '按全部适配角色、套装和主词条保留候选；超出数量目标时按副词条取向压缩重复备用，保留各取向最佳件。首选达到 SSS 时可建议锁定。'),
    'current_drive': ('全角色驱动需求', '全部角色的合法蓝图按形状组合去重后评估备用需求，不把每张蓝图需求直接相加；超出数量目标时按副词条取向压缩重复备用，完整可行配装始终保护。'),
    'future': ('未来用途储备', '先核对套装和主属性用途，再按攻击、生命、防御及治疗方向储备；要求同类基础副属性和至少三个有效副词条。未来假设不撤销已有弃置标记。'),
    'grade': ('驱动评级保护', '按所有适配角色中的最高有效评级判定；达到所选评级的驱动全部保留，不受目标数量或重复备用压缩影响。低于评级不直接弃置；与其他规则冲突仍按列表顺序处理。'),
    'redundant': ('冗余装备弃置候选', '当前用途存在评分候选、未命中保护且整批移除复算不降分时列出弃置建议；可在预览取消采用，不宣称逐件伤害不降。'),
}


def fingerprint(rule):
    # 稳定规则身份用于编辑与导航，不属于匹配条件；迁移补入身份不能误报为已编辑。
    content = {k: v for k, v in rule.items() if k not in {'intelligent', 'rule_id'}}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def automatic_main_supported(rule, fact):
    """未编辑的自动组合不把未知跨元素用途升级为保留；用户规则及已确认光套例外独立保留。"""
    origin = rule.get('intelligent') or {}
    if fact.suit_id == 'Suit11' and str(origin.get('key', '')).startswith('combo:sonic:'):
        return True  # 音速的全队辅助效果不依赖主属性，只有显式的该套保护规则使用此例外。
    if (not origin or origin.get('edited') or origin.get('baseline') != fingerprint(rule)
            or origin.get('key') == 'combo:light' or fact.kind != 'tape' or fact.eligible_roles):
        return True
    from src.domain.equipment.warehouse.warehouse_recommendation import SUIT_ELEMENT_MAINS
    elemental = '属性异能伤害增强' in fact.main_stat or fact.main_stat == '心灵伤害增强%'
    return not elemental or SUIT_ELEMENT_MAINS.get(fact.suit_id) == fact.main_stat


def attach_origin(rule, key):
    result = deepcopy(rule)
    result['intelligent'] = {'key': key, 'version': 1, 'baseline': fingerprint(result), 'edited': False}
    return result


def preserve_origin(original, edited):
    result = deepcopy(edited)
    if original.get('positive_values_only'):
        result.setdefault('positive_values_only', True)
    if original.get('intelligent'):
        result['intelligent'] = deepcopy(original['intelligent'])
        result['intelligent']['edited'] = True
    return result


def remembered_drive_grade(config):
    """现有规则优先；无规则时只恢复用户曾使用的评级，不影响手动规则计算。"""
    rules = [r for r in config.get('preserve_rules', ()) if r.get('smart_kind') == 'grade']
    active = [r for r in rules if r.get('enabled', True)]
    if len(active) == 1:
        return active[0]['grade']
    if rules and not active:
        return 'none'
    options = config.get('recommendation') or {}
    value = options.get('last_drive_grade', options.get('grade'))
    return value if isinstance(value,str) and value in {*GRADE_MIN_RATIOS, 'none'} else None


def generated_rules(options, *, strong_set_name, all_sets=None, all_shapes=None, support_set_name=None):
    """只生成条件与策略，不把本次装备 UID 或配装结果保存为规则。"""
    result = []
    for kind, (name, _description) in KINDS.items():
        grade = options.get('grade', 'SSS')
        result.append(attach_origin({
            'enabled': not (kind == 'grade' and grade == 'none'), 'name': name,
            'smart_kind': kind, 'reserve': 1 if kind == 'current_drive' else 2,
            'grade': grade if grade in GRADE_MIN_RATIOS else 'SSS',
            'action': 'discard' if kind == 'redundant' else 'lock' if kind in {'baseline', 'current_tape', 'current_drive'} else 'keep',
            'item_type': 'drive' if kind in {'current_drive', 'grade'} else 'tape',
            **({'target_inventory_size': options.get('target_inventory_size', 1000)} if kind == 'future' else {}),
        }, 'policy:' + kind))
    from src.domain.inventory.post_actions import merge_post_action_config
    def condition(key, name, mains, pool, required, count, sets=None, kind='tape'):
        rule = {'enabled': True, 'name': name, 'item_type': kind, 'action': 'keep',
                'main_stats': mains, 'sub_stats': sorted(pool), 'required_sub_stats': sorted(required),
                'sub_match': count, 'quality_scope': 'all', 'positive_values_only': True,
                'shape_ids': list(all_shapes) if kind == 'drive' and all_shapes is not None else None,
                'set_names': sets if sets is not None else all_sets}
        normalized = merge_post_action_config({'preserve_rules': [rule]})['preserve_rules'][0]
        result.append(attach_origin(normalized, key))
    crit = {'暴击率%', '暴击伤害%'}
    support = {'伤害增加%', '环合强度', '攻击力%'}
    # 用户确认的优质四词条组合不受数量截断；未证实主属性严重不适配时保守保留。
    for stat, label in [('攻击力%', '攻击'), ('环合强度', '环合')]:
        condition('combo:premium:tape:' + stat, localized_text(None, 'auto.domain.equipment.warehouse.intelligent_rules.generated_rules.f6cd83c0d', '全套装空幕双暴增伤{label}保护', label=f'{label}'), [],
                  crit | {'伤害增加%', stat}, crit, 4)
    condition('combo:premium:tape:倾陷强度', '全套装倾陷主属性双暴增伤倾陷保护', ['倾陷强度'],
              crit | {'伤害增加%', '倾陷强度'}, crit, 4)
    for stat, label in [('攻击力%', '攻击'), ('环合强度', '环合'), ('倾陷强度', '倾陷'),
                        ('生命值%', '生命'), ('防御力%', '防御')]:
        condition('combo:premium:drive:' + stat, localized_text(None, 'auto.domain.equipment.warehouse.intelligent_rules.generated_rules.fc8c93491', '驱动双暴增伤{label}保护', label=f'{label}'), [],
                  crit | {'伤害增加%', stat}, crit, 4, kind='drive')
    for stat, label in [('环合强度', '环合'), ('倾陷强度', '倾陷')]:
        condition('combo:premium:drive:attack:' + stat, localized_text(None, 'auto.domain.equipment.warehouse.intelligent_rules.generated_rules.f176ea0b7', '驱动双暴攻击{label}保护', label=f'{label}'), [],
                  crit | {'攻击力%', stat}, crit, 4, kind='drive')
    if strong_set_name:
        condition('combo:light', '光套双暴及有效副词条储备', [], crit | support, crit, 3, [strong_set_name])
    if support_set_name:
        for stat, label in [('攻击力%', '攻击'), ('防御力%', '防御'), ('生命值%', '生命'),
                            ('环合强度', '环合'), ('倾陷强度', '倾陷')]:
            condition('combo:sonic:' + stat, localized_text(None, 'auto.domain.equipment.warehouse.intelligent_rules.generated_rules.f3d80f73a', '音速辅助双暴增伤{label}保护', label=f'{label}'), [],
                      crit | {'伤害增加%', stat}, crit | {'伤害增加%'}, 4, [support_set_name])
    condition('combo:offense', '输出主属性双暴储备', ['暴击率%', '暴击伤害%', '攻击力%'], crit | support, crit, 3)
    for main, stats, label in [('生命值%', ['生命值%', '生命值'], '生命'), ('防御力%', ['防御力%', '防御力'], '防御')]:
        for stat in stats:
            condition('combo:' + stat, label + '主副属性与双暴储备', [main], crit | support | {stat}, crit | {stat}, 4)
            condition('combo:heal:' + stat, '治疗与' + stat + '双暴储备', ['治疗加成'], crit | support | {stat}, crit | {stat}, 4)
    # 弃置兜底排在全部保护组合之后，用户仍可通过拖动改变实际规则优先级。
    return [r for r in result if r.get('smart_kind') != 'redundant'] + [r for r in result if r.get('smart_kind') == 'redundant']


def without_intelligent_rules(rules):
    return deepcopy([rule for rule in rules if not rule.get('intelligent')])


def merge_generated(existing, generated):
    """重新生成等价于删除全部智能规则后追加本次生成结果；编辑不改变来源。"""
    return without_intelligent_rules(existing) + deepcopy(generated)


def normalize_dynamic(raw):
    kind = raw.get('smart_kind')
    if kind not in KINDS:
        raise ValueError('不支持的智能规则类型，请使用匹配版本。')
    count = raw.get('reserve', 2)
    if type(count) is not int or not 1 <= count <= 20:
        raise ValueError('智能规则保留数量应为 1～20。')
    grade = raw.get('grade', 'SSS')
    if grade not in GRADE_MIN_RATIOS:
        raise ValueError('未知的智能规则保留评级。')
    action = raw.get('action', 'keep')
    if action not in ({'discard'} if kind == 'redundant' else {'keep', 'lock'} if kind in {'baseline', 'current_tape', 'current_drive'} else {'keep'}):
        raise ValueError('此智能规则不支持所选动作。')
    result = {k: deepcopy(v) for k, v in raw.items() if k in {'intelligent'}}
    result.update(smart_kind=kind, name=str(raw.get('name') or KINDS[kind][0]), enabled=bool(raw.get('enabled', True)),
                  reserve=count, grade=grade, action=action, item_type='drive' if kind in {'current_drive', 'grade'} else 'tape')
    if kind == 'future':
        target = raw.get('target_inventory_size', 1000)
        if type(target) is not int or not 0 <= target <= 100000:
            raise ValueError('目标保留总量应为 0～100000，0 表示不限制。')
        result['target_inventory_size'] = target
    return result


def policy_from_rules(rules):
    policy = {}
    for rule in rules:
        if not rule.get('smart_kind') or not rule.get('enabled'):
            continue
        row = normalize_dynamic(rule)
        if row['smart_kind'] in policy:
            raise ValueError('同一种动态智能规则只能启用一条，请关闭重复规则。')
        policy[row['smart_kind']] = row
    return policy


def dynamic_summary(rule):
    kind = rule['smart_kind']
    suffix = ('最低评级：' + rule['grade'] if kind == 'grade' else
              localized_text(None, 'auto.domain.equipment.warehouse.intelligent_rules.dynamic_summary.f57e938de', '{value}：{value_2} 件', value=f"{'额外备用候选' if kind == 'current_drive' else '每用途候选'}", value_2=f"{rule['reserve']}")
              if kind in {'current_tape', 'current_drive', 'future'} else '')
    if kind == 'future':
        target = rule.get('target_inventory_size', 1000)
        suffix += localized_text(None, 'auto.domain.equipment.warehouse.intelligent_rules.dynamic_summary.fe0ba98fc', '；目标保留总量：{target} 件（保护优先）', target=f'{target}') if target else '；总量不限'
    return KINDS[kind][1] + (' ' + suffix if suffix else '')
