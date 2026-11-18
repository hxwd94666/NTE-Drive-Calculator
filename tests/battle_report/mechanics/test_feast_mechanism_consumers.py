# 验证挑战目标经只读目录传至两种公开选择器，组合不冒充完成或改变基础画像。
import sqlite3
from types import SimpleNamespace

import pytest

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from tests.battle_report.targets.feast_target_fixture import feast_database
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


NTE_TEST_TIER = "core"


def test_inference_freezes_all_formal_targets_without_blank_identity_or_goal_completion(tmp_path):
    from src.services.battle_report.targets.encounter.catalog import BattleEncounterCatalogService
    with StaticGameDataDao(goal_database(tmp_path), reuse_connection=False) as dao:
        candidates = BattleEncounterCatalogService._feast_candidates(dao)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert {(target.target_id, target.monster_level, target.max_hp, target.pack_id) for target in candidate.targets} == {
        ('boss_a', 60, 1000, 'boss_a_pack'), ('mon_b', 47, 3000, 'mon_b_pack')}
    assert candidate.feast_options == ()  # 自动候选没有任何机制完成证据。


def goal_database(root):
    path = feast_database(root, version=44)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE feast_stage SET mechanism_category_title='挑战目标',mechanism_score_limit=100")
        for ordinal in range(3):
            identity = f'goal_{ordinal}'
            db.execute("INSERT INTO feast_option VALUES(?,'MechanismBuff','mechanism_goal',NULL,NULL,NULL,100,?,1,?)",
                       (identity, '/Game/Buff/Conditional.Conditional_C', f'条件目标 {ordinal + 1}'))
            db.execute("INSERT INTO feast_stage_option VALUES('stage',?,0,?,?)", (ordinal, f'挑战目标 {ordinal + 1}', identity))
            db.execute("INSERT INTO feast_option_effect VALUES(?,'boss',0,?,1)",
                       (identity, '/Game/Buff/Conditional.Conditional_C'))
    return path


@pytest.mark.parametrize('theme', ["black"])
def test_goal_selection_roundtrips_without_claiming_completion_or_changing_profiles(tmp_path, theme):
    from src.app.theme import apply_app_theme, current_theme_name
    from src.features.battle_report.analysis.target_conditions import BattleTargetConditionSelector
    from src.features.static_catalog.domain_pages.monsters.feast_view import FeastEncounterView
    from src.services.static_catalog.common.terminology import StaticCatalogTerminologyService
    from src.services.static_catalog.monsters.service import StaticCatalogMonsterService
    app = application(); previous = current_theme_name(app); apply_app_theme(app, theme)
    path = goal_database(tmp_path)
    with StaticGameDataDao(path, reuse_connection=False) as dao:
        stages = dao.list_feast_stages()
    assert len(stages[0]['options']) == 3
    assert all(option['effect_sources'][0]['recipient_kind'] == 'boss' for option in stages[0]['options'])
    terms = StaticCatalogTerminologyService(SimpleNamespace(lookup_localized_term=lambda *_a, **_k: None,
                                                           list_fork_campaigns=lambda: ()))
    service = StaticCatalogMonsterService.from_database(path, terminology_service=terms)
    selector = BattleTargetConditionSelector(); view = FeastEncounterView()
    try:
        period = service.list_feast_periods()[0]
        setup = service.get_feast_setup(period.period_id, 'stage')
        assert setup.mechanism_score_limit == 100
        assert len(setup.option_groups) == 3 and '未判定' in setup.condition_note
        detail = service.get_feast_detail(period.period_id, 'stage', 1, boss_monster_id='mon_b',
                                          selected_option_ids=('goal_0', 'goal_1', 'goal_2'))
        assert detail is not None  # 源上限不等于可选数量限制；不猜结算公式。
        assert any(section.title == '挑战目标规则' and '100' in section.note for section in detail.sections)
        selector.set_catalog({'feast': stages})
        selector.environment_combo.setCurrentIndex(selector.environment_combo.findData('feast'))
        selector.feast_target_combo.setCurrentIndex(2)
        before = selector.current_preset()['selected_target_profiles']
        for combo in selector._feast_option_combos.values():
            combo.setCurrentIndex(1)
            assert '条件目标' in combo.currentText() and '未判定完成' in combo.currentText()
        assert selector.current_preset()['selected_target_profiles'] == before
        assert len(selector.current_preset()['feast_options']) == 3
        view.set_stage(setup, icon=None, loader=lambda *_a: detail, blessings=(), blessing_loader=lambda _key: None)
        view.target_combo.setCurrentIndex(1)
        for combo in view._option_combos:
            combo.setCurrentIndex(1)
        assert '上限 100' in view.condition_summary.text() and '未判定' in view.condition_summary.text()
    finally:
        dispose(selector); dispose(view); service.close(); apply_app_theme(app, previous)
