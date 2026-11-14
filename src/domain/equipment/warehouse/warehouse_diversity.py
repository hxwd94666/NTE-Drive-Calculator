# 在数量压力下只压缩当前用途的相似备用件，保留评级、锁定及明确规则保护。
from collections import defaultdict
from math import isfinite


SOFT_PREFIXES = ('当前用途储备：',)
FUTURE_PREFIX = '未来用途候选：'


def diversity_key(item):
    """先固定套装/主属性或驱动形状及实际适配范围，禁止跨资格替代。"""
    return (item.kind, item.suit_id if item.kind == 'tape' else '',
            item.main_stat if item.kind == 'tape' else item.shape_id,
            tuple(sorted(item.eligible_roles)))


def diversity_features(item):
    """保留暴击组合与每种养成方向；无关第四词条不同不自动新增一份储备。"""
    stats = {k for k, v in item.stats if isfinite(v) and v > 0}
    critical = tuple(k for k in ('暴击率%', '暴击伤害%', '伤害增加%') if k in stats)
    directions = [name for name, keys in (
        ('attack', {'攻击力%', '攻击力'}), ('health', {'生命值%', '生命值'}),
        ('defense', {'防御力%', '防御力'}), ('reaction', {'环合强度'}),
        ('topple', {'倾陷强度'}), ('haste', {'技能急速'}),
    ) if stats & keys]
    return {(critical, direction) for direction in directions} or {(critical, 'other')}


def score_ratio(item):
    return max((item.score(role) for role in item.eligible_roles), default=0) / max(1, item.area * 10)


def trim_similar_reserves(items, reasons, coverage, eligible, target, *, protected_uids=(), incomplete=False):
    """只撤销自动备用理由；真正弃置仍经规则顺序、全批配装复算及显式采用。"""
    from src.domain.equipment.warehouse.warehouse_reserve_budget import mandatory_keep
    if not target or incomplete:
        return {'trimmed_uids': [], 'replacement_uids': {}, 'target': target}
    protected = set(protected_uids)
    hard, soft, groups = set(), set(), defaultdict(list)
    for item in items:
        fixed = [r for r in reasons[item.uid] if not r.startswith(SOFT_PREFIXES)]
        if mandatory_keep(item, fixed, eligible, protected, incomplete):
            hard.add(item.uid)
        elif any(r.startswith(SOFT_PREFIXES) for r in reasons[item.uid]):
            soft.add(item.uid)
        if item.uid in hard or item.uid in soft:
            groups[diversity_key(item)].append(item)
    pressure = max(0, len(hard | soft) - target)
    representatives, duplicates = set(), []
    for members in groups.values():
        ranked = sorted(members, key=lambda i: (-score_ratio(i), i.uid))
        covered = set()
        for item in ranked:
            features = diversity_features(item)
            if features - covered:
                representatives.add(item.uid)
                covered.update(features)
            elif item.uid in soft:
                duplicates.append(item)
    removed = {i.uid for i in sorted(duplicates, key=lambda i: (score_ratio(i), i.uid))[:pressure]}
    replacements = {}
    for members in groups.values():
        kept = sorted((i for i in members if i.uid not in removed), key=lambda i: (-score_ratio(i), i.uid))
        for item in members:
            if item.uid in removed:
                replacements[item.uid] = [i.uid for i in kept]
                reasons[item.uid] = [r for r in reasons[item.uid] if not r.startswith(SOFT_PREFIXES)]
    for bucket in coverage:
        before = bucket['required_uids']
        if removed.intersection(before):
            bucket['requested_quota'] = bucket['quota']
            bucket['required_uids'] = [uid for uid in before if uid not in removed]
            bucket['quota'] = len(bucket['required_uids'])
            bucket['diversity_trimmed_uids'] = [uid for uid in before if uid in removed]
    return {'target': target, 'protected': len(hard), 'diversity_minimum': len(hard | representatives),
            'trimmed_uids': sorted(removed), 'replacement_uids': replacements,
            'remaining_overflow': max(0, len(hard | soft) - len(removed) - target)}
