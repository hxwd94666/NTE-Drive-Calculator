# 为冻结建议生成审查信息，只比较已有评分和装备事实，不重新评分或改变建议。
from math import isfinite
from collections import defaultdict


def review_score(row, role=None):
    value = (row.get('current_scores') or {}).get(role) if role else row.get('best_current_score')
    return float(value) if isinstance(value, (int, float)) and isfinite(value) else None


def review_ratio(row):
    score, area = review_score(row), row.get('area')
    return score / (area * 10) if score is not None and isinstance(area, (int, float)) and area > 0 else None


def best_role(row):
    scores = {k: v for k, v in (row.get('current_scores') or {}).items()
              if isinstance(v, (int, float)) and isfinite(v)}
    return max(scores, key=scores.get) if scores else None


def no_worse(candidate, other):
    """此处只声明同类品质和副属性不差，不宣称任意机制或未来角色收益更高。"""
    quality = {'Blue': 0, 'Purple': 1, 'Gold': 2}
    if candidate['kind'] != other['kind'] or quality.get(candidate.get('quality'), 99) > quality.get(other.get('quality'), -1):
        return False
    if candidate['kind'] == 'drive':
        if candidate.get('shape_id') != other.get('shape_id'):
            return False
    elif (candidate.get('suit_id'), candidate.get('main_stat')) != (other.get('suit_id'), other.get('main_stat')):
        return False
    stats = candidate.get('stats') or {}
    return bool(stats) and all((other.get('stats') or {}).get(key, 0) >= value for key, value in stats.items())


def retained_candidates(row, by_uid):
    keys = dict.fromkeys((*row.get('attribute_candidate_uids', ()), *row.get('replacement_uids', ())))
    return [by_uid[uid] for uid in keys
            if uid in by_uid and by_uid[uid]['decision'] != 'discard']


def adopted_candidates(row, by_uid, adopted_targets):
    """候选必须在本次实际采用目标后不带弃置标记；未知状态不可充当保留见证。"""
    role = best_role(row)
    candidates = [other for other in retained_candidates(row, by_uid)
                  if other.get('state_known', True)
                  and adopted_targets.get(other['uid'], 'discarded' if other.get('original_discarded') else 'normal') != 'discarded']
    def order(other):
        score = review_score(other, role) if role else None
        return not no_worse(row, other), score is None, -score if score is not None else 0
    return sorted(candidates, key=order)


def enrich_review_rows(rows):
    """在同类保留集合中补齐属性比较候选；不把搜索范围局限于评分前几名。"""
    groups = defaultdict(list)
    def key(row):
        return (row['kind'], row.get('shape_id')) if row['kind'] == 'drive' else (row['kind'], row.get('suit_id'), row.get('main_stat'))
    for row in rows:
        if row['decision'] != 'discard':
            groups[key(row)].append(row)
    result = []
    for row in rows:
        candidates = []
        if row['decision'] == 'discard':
            for other in groups[key(row)]:
                if no_worse(row, other) and set(row.get('eligible_roles', ())) <= set(other.get('eligible_roles', ())):
                    candidates.append(other['uid'])
        result.append({**row, 'attribute_candidate_uids': candidates})
    return result


def discard_basis(row, by_uid):
    if row['decision'] != 'discard':
        return ''
    if any(no_worse(row, other) for other in retained_candidates(row, by_uid)):
        return '同类属性不差'
    winner = row.get('winning_rule') or {}
    if winner.get('action') == 'discard' and not winner.get('smart_kind'):
        return '命中弃置规则'
    if row.get('similar_reserve_trimmed'):
        return '同类词条重复'
    return '储备预算取舍' if row.get('reserve_budget_excluded') else '同用途属性取舍'


def stat_changes(row, other):
    current, candidate = row.get('stats') or {}, other.get('stats') or {}
    return {key: candidate.get(key, 0) - current.get(key, 0)
            for key in sorted(current.keys() | candidate.keys())
            if candidate.get(key, 0) != current.get(key, 0)}
