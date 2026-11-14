# 首次评级以数量预算为准，复用规则筛选估算，正式损失检查仍在推荐流程执行。
from copy import deepcopy
from src.domain.equipment.scoring.grade_limits import GRADE_LADDER, GRADE_MIN_RATIOS
from src.domain.inventory.post_actions import preserve_rule_matches_item
from src.domain.equipment.warehouse.intelligent_rules import automatic_main_supported, policy_from_rules
from src.domain.equipment.warehouse.warehouse_recommendation import recommend
from src.domain.equipment.warehouse.warehouse_rule_order import apply_ordered_rules
from src.domain.equipment.warehouse.warehouse_targets import recommendation_targets, discarded_after_targets


def advise_drive_grade(facts, items, rules, demands, multiplicity, *, protected_uids=(),
                       sample_grade='SSS', checkpoint=lambda:None):
    policy=policy_from_rules(rules)
    target=policy.get('future',{}).get('target_inventory_size',1000)
    if not target:
        return {'grade':sample_grade or 'SSS','target':0,'fits_target':True,'attempts':[],'method':'quality_without_budget'}
    matched={i.uid:[r for r in rules if preserve_rule_matches_item(i,r) and automatic_main_supported(r,f)]
             for i,f in zip(items,facts)}
    explicit={uid for uid,group in matched.items() if group and group[0].get('action') in {'keep','lock'}}
    fixed=set(protected_uids)|explicit|{f.uid for f in facts if f.locked or f.equipped}
    ratios={f.uid:max((f.score(r) for r in f.eligible_roles),default=-1)/max(1,f.area*10)
            for f in facts if f.kind=='drive' and f.eligible_roles}
    attempts=[]
    for grade in GRADE_LADDER:
        checkpoint()
        grade_uids={uid for uid,ratio in ratios.items() if ratio>=GRADE_MIN_RATIOS[grade]}
        lower=len(fixed|grade_uids)
        attempt={'grade':grade,'grade_drives':len(grade_uids),'minimum_retained':lower}
        attempts.append(attempt)
        if lower>target:
            continue
        trial_rules=deepcopy(rules)
        for rule in trial_rules:
            if rule.get('smart_kind')=='grade':rule.update(enabled=True,grade=grade)
        result=recommend(facts,selected_roles=tuple(demands),demands=demands,
            baseline_uids=frozenset(protected_uids),policy=policy_from_rules(trial_rules),
            known_shape_multiplicity=multiplicity,target_inventory_size=target,
            budget_protected_uids=frozenset(explicit),
            rule_protection={uid:[(r['action'],str(r.get('name') or '保留规则')) for r in group[:1] if r.get('intelligent')]
                             for uid,group in matched.items()})
        apply_ordered_rules(result,items,trial_rules,matched_by_uid=matched,protected_uids=frozenset(fixed))
        for row,fact in zip(result['rows'],facts):
            row.update(original_locked=fact.locked,original_equipped=fact.equipped,original_discarded=fact.discarded)
        retained=len(facts)-len(discarded_after_targets(result['rows'],recommendation_targets(result['rows'])))
        attempt['estimated_retained']=retained
        if retained<=target:
            return {'grade':grade,'target':target,'fits_target':True,'attempts':attempts,'method':'broadest_grade_within_budget'}
    return {'grade':'ACE','target':target,'fits_target':False,'attempts':attempts,'method':'protection_exceeds_budget'}
