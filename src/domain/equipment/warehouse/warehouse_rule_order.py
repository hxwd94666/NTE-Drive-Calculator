# 按列表顺序仲裁已命中的静态和动态规则，独立安全门禁不随排序放开。

from src.domain.localization.catalog import TextCatalog, localized_text


def apply_ordered_rules(result, items, rules, *, matched_by_uid, protected_uids=frozenset(), incomplete=False,
                        text_catalog: TextCatalog | None = None):
    for row, item in zip(result['rows'], items):
        # 前序已决定、冲突、未知或受阻项只作固定上下文，不参与智能区优先级仲裁。
        if row.get('predecessor_fixed'):
            continue
        matched = matched_by_uid[item.uid]
        dynamic = row.get('dynamic_rule_matches') or {}
        model_reasons = list(row['reasons'])
        for position, rule in enumerate(rules, 1):
            if not rule.get('enabled', True):
                continue
            kind = rule.get('smart_kind')
            action = dynamic.get(kind) if kind else rule.get('action') if rule in matched else None
            if action not in {'keep', 'lock', 'discard'}:
                continue
            row['winning_rule'] = {'position': position,
                                   'name': rule.get('name') or localized_text(
                                       text_catalog, 'warehouse.rule_order.unnamed_rule', '未命名规则'),
                                   'action': action, 'smart_kind': kind}
            row['decision'] = action
            row['reasons'] = [localized_text(
                text_catalog, 'warehouse.rule_order.first_match',
                "首条命中规则（第 {position} 条）：{name}",
                position=position, name=row['winning_rule']['name'])]
            if not kind:
                row['reasons'].append(localized_text(
                    text_catalog, f'warehouse.rule_order.action.{action}',
                    {'keep': '命中保留规则', 'lock': '命中锁定规则', 'discard': '命中弃置规则'}[action]))
            else:
                row['reasons'].append(localized_text(
                    text_catalog, 'warehouse.rule_order.dynamic_rule_order', '按已保存顺序执行动态规则'))
                # 以下前缀是储备预算、评级保留等动态理由的内部证据码，筛选仍按原文前缀匹配。
                prefixes = {'baseline': ('全角色',), 'current_tape': ('当前用途',), 'current_drive': ('当前用途',),
                            'future': ('未来用途',), 'grade': ('评级保留',), 'redundant': ('当前用途已有评分候选',)}
                row['reasons'].extend(r for r in model_reasons if r.startswith(prefixes.get(kind, ())))
            # 后续保护不覆盖首条弃置；其原始命中仍在动态证据中，整批合法性另行验证。
            if action == 'discard':
                row['future_guards'] = []
                if incomplete or item.uid in protected_uids:
                    row['decision'] = 'review'
                    row['reasons'].append(localized_text(
                        text_catalog, 'warehouse.rule_order.discard_guard_review',
                        '规则要求弃置，但已有锁定、装备保护或计算证据不足；维持原状态，需核对'))
            break
    discarded = {row['uid'] for row in result['rows'] if row['decision'] == 'discard'}
    for group in [*result.get('current_buckets', ()), *result.get('future_buckets', ())]:
        original = group['required_uids']
        group['overridden_by_earlier_rules'] = [uid for uid in original if uid in discarded]
        group['effective_required_uids'] = [uid for uid in original if uid not in discarded]
