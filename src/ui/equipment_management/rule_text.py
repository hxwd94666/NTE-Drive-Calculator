# 根据规则纯值生成可翻译摘要；规则名称、正式词条和动作 ID 不随界面语言改写。
from src.ui.common.text_catalog import resolve_text_catalog


def _rule_summary_values(values: list[str], limit: int = 2, *, text_catalog=None) -> str:
    values = [str(value) for value in values if str(value)]
    if len(values) <= limit:
        return "、".join(values)
    return resolve_text_catalog(text_catalog).render('ui.rules.summary.more',
        values="、".join(values[:limit]), count=len(values))


def _preserve_rule_summary(rule: dict, *, text_catalog=None) -> str:
    text = resolve_text_catalog(text_catalog).render
    kind = rule.get("smart_kind")
    if kind:
        # 展示按稳定类型读取说明；不调用求解、不修改规则或其持久化名称。
        suffix = ''
        if kind == 'grade':
            suffix = text('ui.rules.summary.minimum_grade', grade=rule['grade'])
        elif kind in {'current_tape', 'current_drive', 'future'}:
            key = 'extra_candidates' if kind == 'current_drive' else 'use_candidates'
            suffix = text('ui.rules.summary.' + key, count=rule['reserve'])
        if kind == 'future':
            target = rule.get('target_inventory_size', 1000)
            suffix += text('ui.rules.summary.target', count=target) if target else text('ui.rules.summary.unlimited')
        return text('ui.rules.summary.kind.' + kind) + (' ' + suffix if suffix else '')
    parts = []
    def values(items):
        return _rule_summary_values(items, text_catalog=text_catalog)
    if rule.get("item_type") == "tape" and rule.get("main_stats"):
        parts.append(text('ui.rules.summary.main', values=values(rule['main_stats'])))
    if rule.get("sub_stats"):
        raw_mode = rule.get("sub_match", "all")
        try:
            count = 4 if raw_mode == 'all' else int(raw_mode)
        except (TypeError, ValueError):
            count = 1
        count = count if count in {1, 2, 3, 4} else 1
        mode = text('ui.rules.summary.any_' + str(count))
        parts.append(text('ui.rules.summary.sub', values=values(rule['sub_stats']), mode=mode))
    if rule.get("required_sub_stats"):
        parts.append(text('ui.rules.summary.required', values=values(rule['required_sub_stats'])))
    return "｜".join(parts) or text('ui.rules.summary.empty')
