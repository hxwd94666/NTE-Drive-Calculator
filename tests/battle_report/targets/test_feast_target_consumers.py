# 验证静态查询、资料详情和两种选择器保留正式多目标，不默认第一画像且不混用属性包。
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from tests.battle_report.targets.feast_target_fixture import feast_database
from src.storage.sqlite.catalog.static_catalog_monster_queries import StaticCatalogMonsterQueries
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


NTE_TEST_TIER = "core"


@pytest.mark.parametrize("version", [40,44])
def test_public_catalog_preserves_single_and_multi_target_identity(tmp_path, version):
    path = feast_database(tmp_path, version=version)
    with StaticGameDataDao(path, reuse_connection=False) as dao:
        stage = dao.list_feast_stages()[0]
    assert len(stage["targets"]) == (2 if version>=44 else 1)
    assert stage["boss_monster_id"] == (None if version>=44 else 'boss_a')
    with StaticCatalogMonsterQueries(path, reuse_connection=False) as dao:
        assert dao.feast_setup('stage')["targets"] == stage["targets"]
        if version>=44:
            assert dao.feast_encounter('stage',1) is None
            assert dao.feast_encounter('stage',1,boss_monster_id='foreign') is None
        row = dao.feast_encounter('stage',1,**({'boss_monster_id':'mon_b'} if version>=44 else {}))
        assert row["monster_level"] == (47 if version>=44 else 60)
        assert row["profile"]["health_base"] == (3000 if version>=44 else 1000)


def test_catalog_index_and_links_distinguish_targets_and_source_period_is_not_retail_history(tmp_path):
    from src.services.static_catalog.common.terminology import StaticCatalogTerminologyService
    from src.services.static_catalog.monsters.service import StaticCatalogMonsterService
    path = feast_database(tmp_path)
    terms = StaticCatalogTerminologyService(SimpleNamespace(lookup_localized_term=lambda *_args, **_kwargs: None,
                                                           list_fork_campaigns=lambda: ()))
    service = StaticCatalogMonsterService.from_database(path, terminology_service=terms)
    try:
        period = service.list_feast_periods()[0]
        assert period.period_id == 'dataset:cn_test_fixture'
        assert period.release_state == 'unscheduled'
        assert service.get_feast_setup('cn_1_3_20260813','stage') is None
        assert service.get_feast_detail(period.period_id,'stage',1) is None
        detail = service.get_feast_detail(period.period_id,'stage',1,boss_monster_id='mon_b')
        assert detail is not None and detail.entry.secondary_id == 'mon_b'
        assert service.get_detail(detail.entry.key).entry.key == detail.entry.key
        with StaticCatalogMonsterQueries(path,reuse_connection=False) as dao:
            rows,total = dao.list_catalog_index(play_mode='feast',as_of_mainland='2026-10-27',limit=100)
        assert total == 2 and {row['identity_3'] for row in rows} == {'boss_a','mon_b'}
        assert len({(row['identity_1'],row['identity_2'],row['identity_3']) for row in rows}) == 2
    finally:
        service.close()


@pytest.mark.parametrize("theme", ["black"])
def test_battle_selector_requires_primary_but_freezes_both_own_profiles(tmp_path, theme):
    from src.app.theme import apply_app_theme, current_theme_name
    from src.domain.battle_report.targets.profiles import BattleTargetCondition
    from src.features.battle_report.analysis.target_conditions import BattleTargetConditionSelector
    app=application(); previous=current_theme_name(app);apply_app_theme(app,theme)
    with StaticGameDataDao(feast_database(tmp_path),reuse_connection=False) as dao:
        stages=dao.list_feast_stages()
    frozen=deepcopy(stages)
    selector=BattleTargetConditionSelector()
    try:
        selector.set_catalog({'feast':stages})
        selector.environment_combo.setCurrentIndex(selector.environment_combo.findData('feast'))
        assert selector.current_preset()['selected_target_ids'] == ()
        selector.feast_target_combo.setCurrentIndex(2)
        preset=selector.current_preset()
        assert preset['primary_target_id']=='mon_b'
        assert set(preset['selected_target_ids'])=={'boss_a','mon_b'}
        assert {(row['static_target_id'],row['monster_level'],row['max_hp'],row['pack_id']) for row in preset['selected_target_profiles']} == {
            ('boss_a',60,1000,'boss_a_pack'),('mon_b',47,3000,'mon_b_pack')}
        condition=BattleTargetCondition('',1,'outer_realm',0,0,(),environment_kind='feast',environment_ref='stage',
                                       primary_target_id='mon_b',selected_target_ids=('boss_a','mon_b'),difficulty_id=1)
        selector.render(condition)
        assert selector.current_preset()['primary_target_id']=='mon_b'
        selector.render(replace(condition,primary_target_id='foreign'))
        assert selector.current_preset()['selected_target_ids']==()
        assert stages==frozen
    finally:
        dispose(selector);apply_app_theme(app,previous)


@pytest.mark.parametrize("theme", ["black"])
def test_catalog_view_requires_target_and_never_reuses_previous_profile(tmp_path, theme):
    from src.app.theme import apply_app_theme, current_theme_name
    from src.features.static_catalog.domain_pages.monsters.feast_view import FeastEncounterView
    from src.services.static_catalog.common.terminology import StaticCatalogTerminologyService
    from src.services.static_catalog.monsters.service import StaticCatalogMonsterService
    app=application();previous=current_theme_name(app);apply_app_theme(app,theme)
    terms=StaticCatalogTerminologyService(SimpleNamespace(lookup_localized_term=lambda *_a,**_k: None,list_fork_campaigns=lambda: ()))
    service=StaticCatalogMonsterService.from_database(feast_database(tmp_path),terminology_service=terms)
    period=service.list_feast_periods()[0]
    setup=service.get_feast_setup(period.period_id,'stage')
    calls=[]

    def load(period_id,stage_id,difficulty_id,options,boss_monster_id=None):
        detail=service.get_feast_detail(period_id,stage_id,difficulty_id,selected_option_ids=options,boss_monster_id=boss_monster_id)
        calls.append((boss_monster_id,detail.entry.secondary_id if detail else None))
        return detail

    view=FeastEncounterView()
    try:
        view.resize(640,600);view.show()
        view.set_stage(setup,icon=None,loader=load,blessings=(),blessing_loader=lambda _key: None)
        assert calls==[] and '请选择' in view.condition_summary.text()
        view.target_combo.setCurrentIndex(1)
        assert calls[-1]==('boss_a','boss_a')
        view.target_combo.setCurrentIndex(2)
        assert calls[-1]==('mon_b','mon_b')
        assert '47' in view.difficulty_combo.currentText()
        before=len(calls)
        view.set_stage(setup,icon=None,loader=load,blessings=(),blessing_loader=lambda _key: None)
        assert len(calls)==before and not view.detail.isVisible()
    finally:
        dispose(view);service.close();apply_app_theme(app,previous)
