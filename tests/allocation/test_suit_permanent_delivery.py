# 验证正式套装审查、真实角色详情和毕业模板的来源闭环，不读取个人账号。
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from src.domain.equipment.properties.suit_permanent import project_suit_permanent_properties, suit_permanent_source_unknown
from src.services.official_role.page import load_official_role_detail
from src.services.official_role.equipment.graduation import graduation_template_with_weight_substats
from src.services.official_role.panel.attributes import calculate_official_role_attribute_summaries, _role_panel_damage_inputs
from src.storage.sqlite.user_data_dao import UserDataDao
from tools.game_data.static_database_suit_permanent import populate_suit_permanent, validate_suit_permanent

ROOT = Path(__file__).resolve().parents[2]
NTE_TEST_TIER = 'core'


def test_real_detail_keeps_suit_source_and_theoretical_proof_without_promoting_virtual_items(tmp_path):
    database = tmp_path/'user.sqlite3'
    with UserDataDao(database, account_id="suit-delivery"):
        pass
    detail = load_official_role_detail(database,1003,include_inventory_contexts=False,
        include_replacement_candidates=False,static_database_path=ROOT/'data/game_static.sqlite3')
    assert len(detail['suit_definitions']) == 12 and len(detail['suit_permanent_reviews']) == 12
    template = graduation_template_with_weight_substats(detail)
    assert template and all('graduation_assumed_shape_ids' in item for item in template['equipment'] if item['kind']=='module')
    assert project_suit_permanent_properties(template['equipment'],detail['suit_definitions'],detail['suit_permanent_properties'])
    assert not suit_permanent_source_unknown(template['equipment'],detail['suit_definitions'],detail['suit_permanent_reviews'])
    virtual = [{**item,'virtual':True} for item in template['equipment']]
    assert not project_suit_permanent_properties(virtual,detail['suit_definitions'],detail['suit_permanent_properties'])
    unknown = deepcopy(detail)
    unknown.update(suit_permanent_reviews={},equipment_contexts={'current':{'items':template['equipment']}})
    assert not _role_panel_damage_inputs(unknown,'current')


@pytest.mark.parametrize('suit,property_id,ratio',[('Suit8','AtkUp',.1),('Suit7','DefUp',.15),('Suit9','HPMaxUp',.1),('Suit5','CritBase',.14),('Suit1','DamagePenetrateChaos',.12)])
def test_public_panel_uses_each_side_candidate_not_current_loadout(suit,property_id,ratio,tmp_path):
    database=tmp_path/'user.sqlite3'
    with UserDataDao(database, account_id="suit-delivery"):
        pass
    detail=load_official_role_detail(database,1003,include_inventory_contexts=False,
        include_replacement_candidates=False,static_database_path=ROOT/'data/game_static.sqlite3')
    shapes=detail['suit_definitions'][suit]['required_shape_ids']
    equipment=[{'kind':'core','suit_id':suit,'main_stats':[],'sub_stats':[]},
        *[{'kind':'module','geometry':shape,'grid_count':1,'sub_stats':[]} for shape in shapes]]
    before=deepcopy(equipment)
    rows=calculate_official_role_attribute_summaries(detail,equipment)['equipment']
    assert {row.key:row.value for row in rows}[property_id] == pytest.approx(ratio)
    # 换装对比、分享与替换均使用这同一公共面板；不向原装备写套装伪词条。
    missing=calculate_official_role_attribute_summaries(detail,equipment[:-1])['equipment']
    if suit in {'Suit5','Suit1'}: assert property_id not in {row.key for row in missing}
    assert equipment == before


def test_formal_import_rejects_changed_evidence_and_rolls_back_on_missing_review(tmp_path):
    source=sqlite3.connect((ROOT/'data/game_static.sqlite3').as_uri()+'?mode=ro',uri=True)
    target=sqlite3.connect(tmp_path/'candidate.sqlite3')
    try:
        source.backup(target)
        validate_suit_permanent(target)
        before=target.execute('SELECT * FROM equipment_suit_permanent_property ORDER BY suit_id,required_count,property_id').fetchall()
        policy=json.loads((ROOT/'tools/game_data/equipment_suit_permanent_reviews.json').read_text(encoding='utf8'))
        policy['reviews'].pop()
        path=tmp_path/'bad-review.json';path.write_text(json.dumps(policy),encoding='utf8')
        with pytest.raises(ValueError): populate_suit_permanent(target,path)
        assert target.execute('SELECT * FROM equipment_suit_permanent_property ORDER BY suit_id,required_count,property_id').fetchall()==before
        target.execute("UPDATE equipment_suit_permanent_property SET value=value+1 WHERE property_id='CritBase'")
        with pytest.raises(ValueError): validate_suit_permanent(target)
    finally:
        target.close();source.close()
