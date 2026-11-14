# 验证首次评级选择预算内最宽松档位，不能用数量目标解除既有保护。
from dataclasses import replace
from src.models.equipment import Drive
from src.domain.equipment.warehouse.warehouse_recommendation import WarehouseFact
from src.domain.equipment.warehouse.warehouse_grade_advice import advise_drive_grade


def fixture(target):
    facts=[WarehouseFact(str(i),'drive','','H_2','',2,'Gold',(('暴击率%',2.0),),
        (('role',score),),('role',)) for i,score in enumerate((19,15,13))]
    items=[Drive(uid=f.uid,shape_id=f.shape_id,area=2,quality='Gold',sub_stats=dict(f.stats),
                 main_stats={'攻击力':42,'生命值':560}) for f in facts]
    rules=[{'smart_kind':kind,'enabled':True,'grade':'SS','action':action,'reserve':1,
            'target_inventory_size':target} for kind,action in
           [('current_drive','keep'),('grade','keep'),('future','keep'),('redundant','discard')]]
    return facts,items,rules


def test_selects_broadest_grade_that_fits_the_requested_quantity():
    for target,expected in ((3,'D'),(2,'SSS'),(1,'ACE')):
        facts,items,rules=fixture(target)
        result=advise_drive_grade(facts,items,rules,{'role':{'H_2':1}},{'H_2':1})
        assert result['grade']==expected and result['fits_target']
        assert result['attempts'][-1]['estimated_retained']<=target


def test_existing_locked_items_take_budget_and_are_not_cancelled_to_hit_target():
    facts,items,rules=fixture(2)
    facts[-1]=replace(facts[-1],locked=True)
    advice=advise_drive_grade(facts,items,rules,{'role':{'H_2':1}},{'H_2':1})
    assert advice['grade']=='ACE' and advice['fits_target']
    facts=[replace(f,locked=True) for f in facts]
    advice=advise_drive_grade(facts,items,rules,{'role':{'H_2':1}},{'H_2':1})
    assert not advice['fits_target'] and advice['method']=='protection_exceeds_budget'
