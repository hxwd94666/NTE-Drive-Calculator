# 验证音速辅助四副词条保护覆盖任意主属性，且不扩散到其他套装或固定值词条。
from dataclasses import replace
import pytest
from src.domain.equipment.warehouse.intelligent_rules import generated_rules, automatic_main_supported
from src.domain.equipment.warehouse.warehouse_recommendation import WarehouseFact
from src.domain.inventory.post_actions import preserve_rule_matches_item
from src.models.equipment import Tape

SONIC='音速蓝刺猬'

def rules():
    return [r for r in generated_rules({'grade':'ACE'},strong_set_name='光套',support_set_name=SONIC)
            if str((r.get('intelligent') or {}).get('key','')).startswith('combo:sonic:')]

@pytest.mark.parametrize('fourth',['攻击力%','防御力%','生命值%','环合强度','倾陷强度'])
@pytest.mark.parametrize('main',['暴击率%','治疗加成','光属性异能伤害增强%','心灵伤害增强%','倾陷强度'])
def test_sonic_requires_four_actual_substats_but_allows_all_mains(fourth,main):
    stats={'暴击率%':1,'暴击伤害%':1,'伤害增加%':1,fourth:1}
    item=Tape(uid='t',area=15,quality='Purple',set_name=SONIC,main_stats=main,sub_stats=stats)
    fact=WarehouseFact('t','tape','Suit11','TAPE_15',main,15,'Purple',tuple(stats.items()),(),())
    matches=[r for r in rules() if preserve_rule_matches_item(item,r)]
    assert len(matches)==1 and matches[0]['action']=='keep'
    assert automatic_main_supported(matches[0],fact)
    if '属性异能' in main or '心灵' in main:
        assert not automatic_main_supported(matches[0],replace(fact,suit_id='Suit1'))

@pytest.mark.parametrize('change',[{'暴击伤害%':0},{'伤害增加%':0},{'攻击力%':0,'攻击力':80}])
def test_main_stat_or_flat_attack_cannot_substitute_required_substat(change):
    stats={'暴击率%':1,'暴击伤害%':1,'伤害增加%':1,'攻击力%':1};stats.update(change)
    stats={k:v for k,v in stats.items() if v}
    item=Tape(uid='t',area=15,quality='Gold',set_name=SONIC,main_stats='暴击伤害%',sub_stats=stats)
    assert not any(preserve_rule_matches_item(item,r) for r in rules())

def test_sonic_rules_are_before_discard_fallback_and_not_other_suits():
    generated=generated_rules({'grade':'ACE'},strong_set_name='光套',support_set_name=SONIC)
    assert generated[-1]['smart_kind']=='redundant'
    item=Tape(uid='t',area=15,quality='Gold',set_name='其他套装',main_stats='暴击率%',
              sub_stats={'暴击率%':1,'暴击伤害%':1,'伤害增加%':1,'攻击力%':1})
    assert not any(preserve_rule_matches_item(item,r) for r in rules())
