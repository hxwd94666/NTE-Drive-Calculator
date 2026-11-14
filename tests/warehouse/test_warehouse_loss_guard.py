# 验证全角色损失逐人检查、有限补回、明确规则冲突和部分采用的保护边界。
from concurrent.futures import CancelledError
from pathlib import Path
from types import SimpleNamespace
import pytest

from src.services.inventory.recommendation.warehouse_loss_guard import LossWitness, plan_losses, repair_known_losses, freeze_loss_witnesses
from src.services.inventory.recommendation.warehouse_recommendation_preview import RecommendationGuard, preview_targets
from src.optimizer.allocation_kernel import AllocationKernelRequest


def row(uid,**extra):
    return {'uid':uid,'decision':'discard','original_locked':False,'original_equipped':False,'original_discarded':False,
            'state_known':True,'reasons':['自动弃置候选'],'winning_rule':{'action':'discard','smart_kind':'redundant'},**extra}


def plan(uid,score=100,core=True):
    return {'valid':True,'score':score,'uids':[uid],'has_core':core}


def test_repairs_known_loss_and_preserves_original_rule_evidence():
    rows=[row('needed'),row('spare',decision='keep')]
    before={'cold':plan('needed')}
    witness=LossWitness('all_roles',before,lambda removed:{'cold':plan('spare',90)} if 'needed' in removed else before)
    result=repair_known_losses(rows,[witness],lambda:None)
    assert result['complete'] and result['restored_uids']==['needed']
    assert rows[0]['decision']=='keep' and rows[0]['pre_loss_winning_rule']['smart_kind']=='redundant'
    assert result['required_retained_uids']==['needed']


def test_repair_handles_allocation_shift_when_losing_roles_own_item_remains():
    rows=[row('a',decision='keep'),row('b'),row('other',decision='keep')]
    before={'A':plan('a'),'B':plan('b')}
    witness=LossWitness('all_roles',before,lambda removed:{'A':plan('a',90),'B':plan('other')} if 'b' in removed else before)
    result=repair_known_losses(rows,[witness],lambda:None)
    assert result['complete'] and result['restored_uids']==['b']


def test_explicit_discard_conflict_is_blocked_not_silently_reordered():
    rows=[row('needed',winning_rule={'position':1,'action':'discard','name':'人工弃置'}),row('other',decision='keep')]
    before={'cold':plan('needed')}
    result=repair_known_losses(rows,[LossWitness('all_roles',before,lambda _: {'cold':plan('other',90)})],lambda:None)
    assert not result['complete'] and result['blocked_uids']==['needed']
    assert rows[0]['decision']=='discard'
    with pytest.raises(ValueError,match='明确弃置规则'):
        preview_targets({'rows':rows,'loss_check':result,'violations':['loss']})


def test_missing_core_is_loss_even_if_total_score_increases_and_preexisting_gap_is_not():
    before={'A':plan('a',100,True),'B':plan('b',100,False)}
    after={'A':plan('a',110,False),'B':plan('b',100,False)}
    assert [r['role'] for r in plan_losses(before,after)]==['A']
    assert plan_losses({'A':plan('a'),'B':plan('b')},{'A':plan('a',120),'B':plan('b',90)})[0]['role']=='B'
    assert plan_losses({'A':plan('a')},{'A':plan('a',float('nan'))})[0]['reasons']==['评分未知']


def test_restore_old_discard_and_partial_adoption_requires_the_verified_inventory():
    rows=[row('needed',decision='review',original_discarded=True,winning_rule=None),row('extra'),row('other',decision='keep')]
    before={'A':plan('needed')}
    witness=LossWitness('single:A',before,lambda removed: {'A':plan('other',90)} if 'needed' in removed else before)
    check=repair_known_losses(rows,[witness],lambda:None)
    targets=preview_targets({'rows':rows})
    assert targets=={'needed':'normal','extra':'discarded'}
    guard=RecommendationGuard(Path('unused'),1,'identity',(),(),{}, {},lambda:None,
                              frozenset(check['required_retained_uids']),frozenset({'needed'}))
    guard.require_targets(targets)
    with pytest.raises(ValueError,match='未保留'):
        guard.require_targets({'extra':'discarded'})
    guard.require_targets({})


def test_cancel_and_round_limit_never_report_unverified_success():
    before={'A':plan('needed')}
    witness=LossWitness('all_roles',before,lambda _: {'A':plan('other',90)})
    assert not repair_known_losses([row('needed'),row('other',decision='keep')],[witness],lambda:None,max_rounds=0)['complete']
    def cancel():raise CancelledError()
    with pytest.raises(CancelledError):repair_known_losses([row('needed')],[witness],cancel)


def test_frozen_scopes_cover_all_roles_two_orders_and_individual_requests():
    requests=[]
    def execute(request):
        requests.append(request)
        return {role:plan(role) for role in request.role_order}
    kernel=SimpleNamespace(execute=execute)
    request=AllocationKernelRequest(inventory=(),roles_db={'hot':{},'cold':{}},sets_db={},shapes_db={},
        blueprints_db={'hot':[],'cold':[]},role_order=('hot','cold'),strategy='role_priority',module_set_targets={},set_effect_modes={},
        core_main_filters={},core_set_targets={},stat_priority_configs={},property_limits={})
    witnesses=freeze_loss_witnesses(kernel.execute,request,{'hot':plan('hot'),'cold':plan('cold')},
        [],lambda:None,lambda:False,lambda result:result)
    assert {w.key for w in witnesses}=={'all_roles','reverse_roles','single:hot','single:cold'}
    assert any(r.role_order==('cold','hot') for r in requests)
    assert all(r.roles_db is request.roles_db for r in requests)


def test_intact_frozen_plan_reuses_evidence_without_resolving():
    rows=[row('needed',decision='keep'),row('extra')]
    before={'A':plan('needed')}
    def should_not_run(_):raise AssertionError('完整冻结方案不应重复求解')
    result=repair_known_losses(rows,[LossWitness('all_roles',before,should_not_run)],lambda:None)
    assert result['complete'] and result['verification_solver_calls']==0
    assert result['rounds'][0]['methods']=={'all_roles':'baseline_retained'}
    assert result['after']['all_roles']==before and result['after']['all_roles'] is not before


def test_rejects_result_that_claims_removed_equipment_is_available():
    rows=[row('needed')]
    before={'A':plan('needed')}
    with pytest.raises(ValueError,match='已移除'):
        repair_known_losses(rows,[LossWitness('all_roles',before,lambda _:before)],lambda:None)
