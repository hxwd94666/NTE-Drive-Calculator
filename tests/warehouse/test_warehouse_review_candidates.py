# 同一流程的公共行为回归合并，保留各自数据、失败与回滚断言。


# 验证候选按最终采用状态保留且属性支配优先，并冻结公共评分器的实际权重。
from types import SimpleNamespace

from src.domain.equipment.warehouse.warehouse_review import adopted_candidates
from src.services.inventory.recommendation.warehouse_recommendation_display import freeze_recommendation_stat_weights
from src.features.inventory.warehouse.display.recommendation_cards import recommendation_item_view


def row(uid, **extra):
    return dict(uid=uid,kind='drive',shape_id='H_3',quality='Gold',decision='keep',
                stats={'攻击力%':1,'暴击伤害%':20},current_scores={'角色':30},original_discarded=False,
                **extra)


def test_only_candidates_retained_after_adoption_and_no_worse_first():
    original=row('current',replacement_uids=['trade','dominant','restore','unknown'])
    trade={**row('trade'),'stats':{'攻击力%':10},'current_scores':{'角色':100}}
    dominant={**row('dominant'),'stats':{'攻击力%':2,'暴击伤害%':20}}
    restore={**row('restore'),'original_discarded':True,'current_scores':{'角色':50}}
    unknown={**row('unknown'),'state_known':False}
    lookup={r['uid']:r for r in (original,trade,dominant,restore,unknown)}
    assert [r['uid'] for r in adopted_candidates(original,lookup,{})]==['dominant','trade']
    assert [r['uid'] for r in adopted_candidates(original,lookup,{'restore':'normal'})]==['restore','dominant','trade']
    assert [r['uid'] for r in adopted_candidates(original,lookup,{'dominant':'discarded'})]==['trade']


def test_frozen_weights_use_public_resolver_and_formal_property_ids():
    prepared=[]
    scorer=SimpleNamespace(roles_db={'角色':{'weights':{'暴击率%':.9},'main_weights':{'暴击率%':.5}}},
        prepare_weight_queries=lambda weights,names:prepared.append((weights,names)),
        flexible_weight=lambda name,weights:weights.get(name,0))
    result=freeze_recommendation_stat_weights(scorer,[{'main_stats':[{'property_id':'CritBase'}],
                                                     'sub_stats':[{'property_id':'CritBase'}]}])
    assert prepared and result['角色']=={'sub':{'CritBase':.9},'main':{'CritBase':.5}}
    raw={'kind':'core','quality':'orange','level':20,'main_stats':[{'property_id':'CritBase','value':.3,'percent':True}],
         'sub_stats':[{'property_id':'CritBase','value':.1,'percent':True}]}
    value={'kind':'tape','set_name':'套装','display_item':raw,'current_scores':{'角色':30}}
    view=recommendation_item_view(value,result['角色'])
    assert view['main_stats'][0]['weight']==.5 and view['sub_stats'][0]['weight']==.9
    assert 'weight' not in raw['sub_stats'][0]

# 验证建议卡片满级主属性沿用公共投影，保留真实等级和副属性，缺曲线不宣称满级。
from copy import deepcopy
from types import SimpleNamespace

from src.services.equipment.equipment_level_projection_service import project_equipment_items_to_max_level
from src.features.inventory.warehouse.display.recommendation_cards import recommendation_item_view


def test_max_main_projection_preserves_source_and_display_percent():
    raw = dict(kind='core',item_id='core',quality='orange',grid_count=15,level=0,max_level=20,
               main_stats=[dict(property_id='CritBase',value=.06,percent=True)],
               sub_stats=[dict(property_id='CritDamageBase',value=.2,percent=True)])
    frozen = deepcopy(raw)
    calls = []
    def curve(identity, level):
        calls.append((identity,level))
        return .30
    dao = SimpleNamespace(list_equipment_items=lambda:[{'item_id':'core','max_level':20}],
                          evaluate_equipment_base_attribute_curve=curve)
    projected = project_equipment_items_to_max_level([raw], dao, include_projection_evidence=True)[0]
    assert calls == [('CritBase_Core_ITEM_QUALITY_ORANGE',20)]
    assert raw == frozen and projected['sub_stats'] == raw['sub_stats']
    assert projected['main_stats_at_max_level']
    display = {**raw, 'main_stats':projected['main_stats'], 'main_stats_at_max_level':True}
    view = recommendation_item_view(dict(kind='tape', set_name='套装', display_item=display, current_scores={}))
    assert view['level'] == 0 and view['main_stats'][0]['value'] == '+30%'
    assert view['main_stats_at_max_level']


def test_missing_curve_or_max_level_cannot_be_labeled_full_level():
    raw = dict(kind='module',item_id='drive',quality='purple',grid_count=3,level=1,max_level=16,
               main_stats=[dict(property_id='AtkAdd',value=12)])
    dao = SimpleNamespace(list_equipment_items=lambda:[],evaluate_equipment_base_attribute_curve=lambda *_:None)
    result = project_equipment_items_to_max_level([raw],dao,include_projection_evidence=True)[0]
    assert not result['main_stats_at_max_level'] and result['main_stats']==raw['main_stats']
    dao.evaluate_equipment_base_attribute_curve = lambda *_: 80
    result = project_equipment_items_to_max_level([{**raw,'max_level':0}],dao,include_projection_evidence=True)[0]
    assert not result['main_stats_at_max_level']
