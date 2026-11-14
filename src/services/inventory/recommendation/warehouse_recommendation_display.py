# 从本次公共评分器冻结词条权重，界面只消费数值，不按显示名重新推导评分。
from src.services.allocation.inputs.inventory import legacy_stat_name


def freeze_recommendation_stat_weights(scorer, originals):
    names = {}
    for item in originals:
        for stat in [*(item.get('main_stats') or ()), *(item.get('sub_stats') or ())]:
            identity = str(stat.get('property_id') or '')
            name = legacy_stat_name(identity)
            if name:
                names[identity] = name
    profiles = {}
    queries = []
    for role, data in scorer.roles_db.items():
        sub = data.get('weights') or {}
        main = data.get('main_weights')
        profiles[role] = {'sub': sub, 'main': sub if main is None else main}
        queries.extend(profiles[role].values())
    scorer.prepare_weight_queries(queries, tuple(names.values()))
    return {role: {group: {identity: scorer.flexible_weight(name, weights) for identity, name in names.items()}
                   for group, weights in profile.items()} for role, profile in profiles.items()}
