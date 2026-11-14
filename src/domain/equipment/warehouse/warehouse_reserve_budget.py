# 按目标保留总量选择额外未来储备，必要保护优先，每个储备组完整纳入或跳过。
from collections import defaultdict, deque


def mandatory_keep(item, reasons, eligible, protected_uids, incomplete):
    fixed = any(not r.startswith('未来用途候选：') for r in reasons)
    if item.uid in protected_uids or item.locked or item.equipped or fixed:
        return True
    # 原已弃置且没有恢复理由的待核对项不会恢复正常，不占保留数量。
    return not item.discarded and (incomplete or item.uid not in eligible)


def apply_reserve_budget(items, reasons, buckets, eligible, target, *, incomplete=False, protected_uids=()):
    prefix = '未来用途候选：'
    protected_uids = set(protected_uids)
    hard = {i.uid for i in items if mandatory_keep(i, reasons[i.uid], eligible, protected_uids, incomplete)}
    if not target:
        return {'target': 0, 'protected': len(hard), 'budget_limited': False}
    by_uid = {i.uid: i for i in items}
    queues = defaultdict(list)
    for bucket in buckets:
        queues[bucket['profile']].append(bucket)
    # 不同用途轮流分配名额；各用途内按公共评分的面积归一值排序，同分按稳定身份排序。
    for profile, rows in queues.items():
        rows.sort(key=lambda row: (-max((by_uid[u].score(profile) / max(1, by_uid[u].area * 10)
                                        for u in row['required_uids']), default=0), tuple(row['required_uids'])))
        queues[profile] = deque(rows)
    kept = set(hard)
    accepted = set()
    while any(queues.values()):
        for profile in sorted(queues):
            if not queues[profile]:
                continue
            row = queues[profile].popleft()
            extra = set(row['required_uids']) - kept
            if not extra or len(kept) + len(extra) <= target:
                kept.update(extra)
                accepted.add(id(row))
    skipped = 0
    for row in buckets:
        if id(row) in accepted:
            continue
        skipped += 1
        row['budget_limited'] = True
        row['requested_quota'] = row['quota']
        row['quota'] = 0
        row['required_uids'] = []
    for item in items:
        if item.uid not in kept:
            reasons[item.uid] = [r for r in reasons[item.uid] if not r.startswith(prefix)]
    return {'target': target, 'protected': len(hard), 'future_extra_kept': len(kept - hard),
            'protected_overflow': max(0, len(hard) - target), 'budget_limited': bool(skipped),
            'skipped_groups': skipped}
