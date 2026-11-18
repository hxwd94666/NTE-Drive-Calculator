# 在冻结库存上验证全角色已知配装，按需撤回自动弃置并保留逐轮证据。
from src.observability.stage_timings import timed, timed_call
from dataclasses import dataclass, replace
from collections.abc import Callable
from copy import deepcopy
from math import isfinite

from src.domain.equipment.warehouse.warehouse_targets import recommendation_targets, discarded_after_targets
from src.domain.localization.catalog import localized_text
from src.services.inventory.recommendation.warehouse_recommendation_profiles import run_profile_removal


@dataclass(frozen=True)
class LossWitness:
    key: str
    before: dict
    evaluate: Callable[[set[str]],dict]


def plan_losses(before, after, *, text_catalog=None):
    losses=[]
    for role, old in before.items():
        if not old.get('valid'):
            continue
        if not isinstance(old.get('score'),(int,float)) or not isfinite(old['score']):
            raise ValueError('配装基线评分不完整，不能生成弃置建议')
        new=after.get(role) or {}
        score=new.get('score')
        reasons=[]
        if not new.get('valid'):
            reasons.append(localized_text(
                text_catalog, 'inventory.warehouse_loss_guard.reason.plan_invalid', '方案失效'))
        if old.get('has_core') and not new.get('has_core'):
            reasons.append(localized_text(
                text_catalog, 'inventory.warehouse_loss_guard.reason.core_missing', '空幕缺失'))
        if not isinstance(score,(int,float)) or not isfinite(score):
            reasons.append(localized_text(
                text_catalog, 'inventory.warehouse_loss_guard.reason.score_unknown', '评分未知'))
        elif score+1e-7<old['score']:
            reasons.append(localized_text(
                text_catalog, 'inventory.warehouse_loss_guard.reason.score_regressed', '得分下降'))
        if reasons:
            losses.append({'role':role,'before_score':old['score'],'after_score':score,'reasons':reasons})
    return losses


@timed("witness.establish")
def freeze_loss_witnesses(execute, request, before, profiles, checkpoint, cancelled, summarize,
                          *, solve_profile=None, timings=None):
    witnesses=[]
    def add(key, pinned, baseline=None):
        checkpoint()
        phase = "witness.single" if key.startswith("single:") else "witness.reverse"
        initial=summarize(timed_call(timings, phase, execute, pinned)) if baseline is None else baseline
        def evaluate(removed):
            checkpoint()
            result=summarize(execute(replace(pinned,inventory=tuple(i for i in pinned.inventory if i.uid not in removed))))
            used=[uid for row in result.values() if row.get('valid') for uid in row.get('uids',())]
            if len(used)!=len(set(used)):
                raise ValueError('全角色配装出现重复装备身份，不能生成弃置建议')
            return result
        witnesses.append(LossWitness(key,initial,evaluate))
    roles=request.role_order
    if set(roles)!=set(request.roles_db):
        raise ValueError('全角色检查缺少角色，不能生成弃置建议')
    add('all_roles',request,before)
    alternate=tuple(reversed(roles))
    if alternate!=roles:add('reverse_roles',replace(request,role_order=alternate))
    for role in roles:
        add('single:'+role,replace(request,role_order=(role,),blueprints_db={role:request.blueprints_db.get(role,())}))
    for context, baseline in profiles:
        def evaluate_profile(removed,context=context):
            checkpoint()
            return run_profile_removal(context, removed, cancelled, solve=solve_profile)
        witnesses.append(LossWitness('profile:'+str(context.profile_id),baseline,evaluate_profile))
    return witnesses


@timed("loss.repair")
def repair_known_losses(rows, witnesses, checkpoint, *, max_rounds=4, progress=lambda _:None, timings=None,
                        text_catalog=None):
    """只撤销自动弃置；明确弃置规则冲突或预算耗尽时保留失败证据、禁止采用。"""
    if not witnesses or type(max_rounds) is not int or max_rounds<0:
        raise ValueError('缺少有效的配装校验范围或轮次预算')
    lookup={row['uid']:row for row in rows}
    restored=set();blocked=set();rounds=[];final_after={};final_losses=[]
    verified={};solver_calls=0
    def plan_uids(plans):
        return {uid for plan in plans.values() if plan.get('valid') for uid in plan.get('uids',())}
    for witness in witnesses:
        for plan in witness.before.values():
            if plan.get('valid') and (not plan.get('uids') or not set(plan['uids'])<=lookup.keys()):
                raise ValueError('已知配装缺少完整装备证据，不能生成弃置建议')
        plan_losses(witness.before,witness.before,text_catalog=text_catalog)  # 校验基线数值，不能把未知当作零分证明。
    def restore(uids, message):
        changed=False
        for uid in sorted(uids):
            checkpoint()
            row=lookup.get(uid)
            winner=(row or {}).get('winning_rule') or {}
            if (not row or row.get('predecessor_fixed') or row.get('original_locked') or not row.get('state_known',True)
                    or winner.get('action')=='discard' and not winner.get('smart_kind')):
                blocked.add(uid)
                continue
            row['pre_loss_decision']=row['decision']
            row['pre_loss_winning_rule']=row.pop('winning_rule',None)
            row['decision']='keep';row['loss_restored']=True
            row['reasons']=[message,*row['reasons']]
            restored.add(uid);changed=True
        return changed
    def needed_plan_uids(scopes):
        return {uid for witness in witnesses if witness.key in scopes
                for plan in witness.before.values() if plan.get('valid') for uid in plan.get('uids',())}
    for round_index in range(max_rounds+1):
        checkpoint()
        removed=discarded_after_targets(rows,recommendation_targets(rows))
        losses=[];after={};needed=set();methods={}
        progress(localized_text(
            text_catalog, 'inventory.warehouse_loss_guard.progress.round',
            '全角色配装校验：第 {round} 轮，已补回 {restored} 件',
            round=round_index+1, restored=len(restored)))
        for witness in witnesses:
            checkpoint()
            certificate=verified.get(witness.key)
            if certificate is not None and not plan_uids(certificate)&removed:
                current=deepcopy(certificate)
                methods[witness.key]='verified_plan_retained'
            elif not plan_uids(witness.before)&removed:
                # 评分、约束和输入已冻结，整份合法方案仍在即证明原分数仍可达，不冒充重新求解。
                current=deepcopy(witness.before)
                methods[witness.key]='baseline_retained'
            else:
                current=timed_call(timings, "loss.recompute", witness.evaluate, removed)
                methods[witness.key]='recomputed'
                solver_calls+=1
            if not plan_uids(current)<=lookup.keys() or plan_uids(current)&removed:
                raise ValueError('配装校验证据引用已移除或仓库外装备')
            after[witness.key]=current
            group_losses=plan_losses(witness.before,current,text_catalog=text_catalog)
            if not group_losses:verified[witness.key]=deepcopy(current)
            for loss in group_losses:
                losses.append({'scope':witness.key,**loss})
                needed.update(witness.before[loss['role']].get('uids',()))
        rounds.append({'round':round_index,'restored_count':len(restored),'losses':losses,'methods':methods})
        final_after,final_losses=after,losses
        if not losses:break
        if round_index==max_rounds:break
        affected_scopes={loss['scope'] for loss in losses}
        additions=(needed & removed)-blocked
        if not additions:
            additions=(needed_plan_uids(affected_scopes) & removed)-blocked
        changed=restore(additions,localized_text(
            text_catalog, 'inventory.warehouse_loss_guard.restore.plan_equipment',
            '全角色配装损失保护：补回已验证方案所需装备'))
        if not changed:
            # 分配重排也可能影响未丢原件的角色，因此再检查整份已知方案所需集合。
            fallback=(needed_plan_uids(affected_scopes) & removed)-blocked-restored
            if not restore(fallback,localized_text(
                    text_catalog, 'inventory.warehouse_loss_guard.restore.team_plan',
                    '全角色配装损失保护：补回已验证整队方案')):
                break
    if timings is not None:
        timings.note("loss.repair", round_count=len(rounds), recompute_count=solver_calls,
                     reused_count=sum(method != "recomputed" for r in rounds for method in r["methods"].values()))
    required={uid for plans in final_after.values() for plan in plans.values() if plan.get('valid') for uid in plan.get('uids',())}
    return {'version':1,'complete':not final_losses,'rounds':rounds,'restored_uids':sorted(restored),
            'blocked_uids':sorted(blocked),'losses':final_losses,'required_retained_uids':sorted(required),
            'before':{w.key:w.before for w in witnesses},'after':final_after,
            'verification_solver_calls':solver_calls,
            'scopes':[w.key for w in witnesses]}
