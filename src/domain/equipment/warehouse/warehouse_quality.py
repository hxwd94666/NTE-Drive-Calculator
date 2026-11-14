# 按全角色去重蓝图的平均需求抽取高分样本，仅计数排序，不执行配装搜索。
from collections import defaultdict
from math import floor, isfinite


def summarize_warehouse_quality(facts, demands, *, core_target=25):
    totals = defaultdict(float)
    for role_demands in demands.values():
        for shape, count in role_demands.items():
            if not isfinite(count) or count < 0:
                raise ValueError('蓝图平均需求无效')
            totals[shape] += count
    # 非负数量采用通常的四舍五入，不使用 Python 的偶数舍入。
    quotas = {shape: floor(count + 0.5 + 1e-9) for shape, count in sorted(totals.items())}
    groups = defaultdict(list)
    used = set()
    for fact in facts:
        if fact.uid in used:
            raise ValueError('仓库质量统计出现重复装备身份')
        used.add(fact.uid)
        scores = dict(fact.scores)
        values = [scores[r] for r in fact.eligible_roles if r in scores and isfinite(scores[r])]
        if not values or fact.area <= 0:
            continue
        key = 'core' if fact.kind == 'tape' else fact.shape_id
        groups[key].append((max(values) / (fact.area * 10), fact.uid))
    def take(key, count):
        return sorted(groups[key], key=lambda item: (-item[0], item[1]))[:count]
    cores = take('core', core_target)
    by_shape = {shape: take(shape, count) for shape, count in quotas.items()}
    drives = [item for group in by_shape.values() for item in group]
    core_rate = sum(rate for rate, _ in cores) / len(cores) if cores else None
    drive_rate = sum(rate for rate, _ in drives) / len(drives) if drives else None
    return {'version':2, 'core_rate':core_rate, 'drive_rate':drive_rate,
            'core_target':core_target, 'core_count':len(cores), 'drive_count':len(drives),
            'shape_targets':quotas, 'shape_counts':{shape:len(items) for shape, items in by_shape.items()},
            'sample_uids':[uid for _, uid in cores + drives],
            'score':core_rate * 150 + drive_rate * 200 if core_rate is not None and drive_rate is not None else None}
