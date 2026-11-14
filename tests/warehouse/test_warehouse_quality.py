# 验证质量分只按蓝图平均数量抽样，及用户评级优先于首次推荐。
from dataclasses import replace
import pytest
from src.domain.equipment.warehouse.warehouse_quality import summarize_warehouse_quality
from src.domain.equipment.warehouse.warehouse_recommendation import WarehouseFact
from src.domain.equipment.warehouse.intelligent_rules import remembered_drive_grade


def item(uid,score,shape='H_2',area=2,kind='drive'):
    return WarehouseFact(uid,kind,'Suit1',shape,'',area,'Gold',(),(('role',score),),('role',))


def test_per_shape_rounding_and_normalized_ranking_without_allocation():
    facts=[item('low',10),item('high',20),item('second',18),item('large',27,'H_3',3),
           item('tape',120,'TAPE_15',15,'tape')]
    result=summarize_warehouse_quality(facts,{'A':{'H_2':0.75,'H_3':0.5},'B':{'H_2':0.75}})
    assert result['shape_targets']=={'H_2':2,'H_3':1}
    assert result['core_count']==1 and result['core_rate']==0.8
    assert result['drive_rate']==pytest.approx((1+.9+.9)/3)
    assert result['score']==pytest.approx(120+(1+.9+.9)/3*200)
    assert set(result['sample_uids'])=={'high','second','large','tape'}


def test_core_sample_is_top_25_and_does_not_depend_on_equipped_status():
    facts=[item(str(i),i,'TAPE_15',15,'tape') for i in range(30)]
    result=summarize_warehouse_quality(facts,{})
    assert result['core_count']==25 and set(result['sample_uids'])=={str(i) for i in range(5,30)}
    changed=[replace(i,equipped=True,discarded=True) for i in facts]
    assert summarize_warehouse_quality(changed,{})==result


def test_unknown_scores_and_missing_samples_do_not_become_zero_or_full_coverage():
    result=summarize_warehouse_quality([replace(item('unknown',20),eligible_roles=())],{'A':{'H_2':2}})
    assert result['score'] is None and result['core_rate'] is None and result['drive_rate'] is None
    assert result['shape_targets']['H_2']==2 and result['shape_counts']['H_2']==0
    with pytest.raises(ValueError,match='重复'):
        summarize_warehouse_quality([item('same',20),item('same',20)],{})


def test_existing_or_remembered_grade_is_never_recommended_again():
    assert remembered_drive_grade({}) is None
    assert remembered_drive_grade({'recommendation':{'grade':'SS'}})=='SS'
    assert remembered_drive_grade({'recommendation':{'last_drive_grade':'ACE'}})=='ACE'
    config={'recommendation':{'last_drive_grade':'ACE'},'preserve_rules':[{'smart_kind':'grade','enabled':True,'grade':'SS'}]}
    assert remembered_drive_grade(config)=='SS'
    config['preserve_rules'][0]['enabled']=False
    assert remembered_drive_grade(config)=='none'
