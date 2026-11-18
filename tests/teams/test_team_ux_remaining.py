# 验证剩余队伍UX的只读来源、目标草稿、结构化历史、增强入口和明确键盘动作。
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3

import pytest
from PySide6.QtCore import Qt, QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox, QWidget, QVBoxLayout

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from tests.teams.matching.team_report_fixture import wait_for
from tests.teams.matching.team_report_fixture import drain_dialog
from tests.teams.overview.team_overview_fixture import overview_case
from src.app.theme import apply_app_theme, current_theme_name
from src.domain.localization.catalog import make_catalog
from src.domain.teams.models import TeamRole
from src.domain.teams.overview import MemberOverview
from src.domain.teams.graduation.axis_graduation_configuration import GraduationConfiguration, ConfigurationMember, LayoutTemplate
from src.features.teams.loadouts.loadout_row import TeamLoadoutRow
from src.features.teams.loadouts.loadout_dialog import TeamLoadoutDialog
from src.features.teams.graduation.axis_graduation_dialog import TeamAxisGraduationDialog
from src.features.teams.graduation.axis_graduation_target_dialog import GraduationTargetDialog
from src.features.teams.graduation.axis_graduation_controller import TeamAxisGraduationController
from src.features.teams.enhancement_history_dialog import TeamEnhancementHistoryDialog
from src.features.allocation.team_axis.team_axis_controls import build_team_axis_controls, apply_team_axis_selection
from src.integrations.preferences.locale_files import bundled_text_catalog
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.teams.graduation.test_team_axis_graduation_history_ui import case
from tests.teams.graduation.test_team_axis_graduation_history_ui import cleanup
from tests.teams.graduation.test_team_axis_graduation_requests import prepared
from tests.teams.enhancement.test_team_enhancement_history import save_result
from tests.allocation.test_team_axis_enhancement import teams
from src.services.allocation.axis.team_axis_selection import TeamAxisBinding, TeamAxisSelection


def sample_catalog():
    return make_catalog(json.loads((Path(__file__).parents[2]/'docs/examples/locales/qps-ploc.json').read_text(encoding='utf8')), base=bundled_text_catalog())


def configuration():
    frozen, values = prepared()
    target = values['target']
    preset = target.presets[0]
    # 这是纯编辑视图夹具，不声称重新证实游戏合法域或执行独立分析。
    rules = SimpleNamespace(**{key:getattr(target,key) for key in ('dataset_id','static_schema','static_sha256','scoring_rule_sha256')})
    members=[]; templates=[]
    for index, (member, layout) in enumerate(zip(preset.members,preset.layouts)):
        slots=index+1
        levels=tuple((skill,((level,member.breakthrough_stage,member.awakening_level),)) for skill,level in member.skill_levels)
        members.append(ConfigurationMember(f'角色 {member.character_id}',member,((member.character_level,member.breakthrough_stage),),levels,slots))
        templates.append(LayoutTemplate(member.character_id,slots,slots,'合成完整图纸',layout))
    return GraduationConfiguration(frozen.account_id,frozen.generation,frozen.team,rules,target.target_id,1,tuple(members),tuple(templates),target.world_bonus)


def dump(path):
    with sqlite3.connect(path) as db:return tuple(db.iterdump())


@pytest.mark.parametrize('choices,selected,key', [
    ((),None,'no_saved'),
    ((dict(character_id=1,slot_id=1,slot_name='空',current_plan_id=None),),1,'empty_slot'),
    ((dict(character_id=1,slot_id=1,slot_name='主力',current_plan_id=11),dict(character_id=1,slot_id=2,slot_name='备用',current_plan_id=None)),None,'pick_slot'),
    ((dict(character_id=1,slot_id=1,slot_name='主力',current_plan_id=11),),99,'invalid'),
])
def test_row_distinguishes_empty_multiple_and_invalid_without_retargeting(choices,selected,key):
    application();catalog=bundled_text_catalog();row=TeamLoadoutRow(1,TeamRole(1,'成员'),catalog=catalog)
    try:
        row.populate(choices,selected);row.show_member(MemberOverview(1,'成员',issues=('合成来源缺项',)))
        assert row.status.text()==catalog.render('teams.loadout.'+key)
        assert row.choice.currentData()==selected
        assert row.description.accessibleName() and row.description.toolTip()
        if key=='empty_slot':assert '空槽位' in row.choice.currentText()
    finally:dispose(row)


@pytest.mark.parametrize('percent',[None,0.0,113.42])
def test_graduation_hero_uses_structured_ratio_and_never_masks_qt_metric(tmp_path,percent):
    service,_inputs,client,_values,team=case(tmp_path);owner=TeamAxisGraduationController(service_provider=lambda:service)
    dialog=TeamAxisGraduationDialog(owner=owner,team=team)
    try:
        dialog.open();wait_for(lambda:not owner.is_running());calls=len(client.operations)
        view=replace(owner.history.display,ratio_percent=percent,headline='合成数学事实：不从文字取数')
        dialog._display(view);application().processEvents()
        assert callable(dialog.metric)  # QPaintDevice 的虚方法必须保留，不能被 QLabel 覆盖。
        assert dialog.ratio_label.text()==('毕业率未知' if percent is None else f'{percent:.2f}%')
        assert dialog.ratio_label.accessibleName()==dialog.ratio_label.text()
        assert dialog.headline.text()==view.headline and len(client.operations)==calls
        assert dialog.advanced.content.isHidden()
    finally:cleanup(owner,dialog)


def test_target_editor_only_returns_explicit_draft_and_preserves_frozen_configuration():
    application();config=configuration();before=repr(config);dialog=GraduationTargetDialog(configuration=config)
    try:
        dialog.open();application().processEvents()
        first=next(iter(dialog.growth.values()));first.setFocus();QTest.keyClick(first,Qt.Key_Return)
        assert dialog.target is None and repr(config)==before
        dialog.buttons.button(QDialogButtonBox.Ok).click()
        assert dialog.target is not None and dialog.target.character_ids==config.team.character_ids
        assert dialog.target.world_bonus==config.world and repr(config)==before
    finally:dispose(dialog)


@pytest.mark.parametrize('theme',["black"])
@pytest.mark.parametrize('width',[560])
def test_remaining_windows_long_patch_actions_stay_in_available_area(tmp_path,monkeypatch,theme,width):
    from src.app import window_geometry
    app=application();previous=current_theme_name();apply_app_theme(app,theme)
    monkeypatch.setattr(window_geometry,'_available_geometry',lambda _dialog:QRect(0,0,width,820))
    catalog=sample_catalog();service,team,slots=overview_case(tmp_path)
    loadout=TeamLoadoutDialog(service=service,team=team,roles={},catalog=catalog)
    try:
        loadout.open();wait_for(lambda:loadout._expected is not None and not loadout.is_running());app.processEvents()
        for button in loadout.buttons.buttons():
            assert button.isVisible() and loadout.rect().contains(button.mapTo(loadout,button.rect().bottomRight()))
        assert loadout.frameGeometry().right()<width
        for row in loadout.rows.values():
            assert row.choice.height() >= row.choice.minimumSizeHint().height()
            assert row.choice.geometry().bottom() < row.description.geometry().top()
            assert not row.description.wordWrap()
            loadout.scroll.ensureWidgetVisible(row.choice)
            app.processEvents()
            assert not row.choice.visibleRegion().isEmpty()
        save_result(service,team,slots,before=100,after=95)
        history=TeamEnhancementHistoryDialog(service=service,team=team,catalog=catalog)
        try:
            history.open();wait_for(lambda:history._rows and not history.is_running())
            assert '-5.00%' in history.headline.text()
            assert len([history.tabs.tabText(i) for i in range(history.tabs.count())])==4
            assert history.technical_section.content.isHidden()
            for button in [history.more,*history.buttons.buttons()]:assert history.rect().contains(button.mapTo(history,button.rect().bottomRight()))
            assert '静态来源' in history.technical.text()
        finally:drain_dialog(history);dispose(history)
    finally:
        drain_dialog(loadout);dispose(loadout);apply_app_theme(app,previous)




def test_mixed_loadout_preview_reports_empty_and_unselected_and_cancel_is_readonly(tmp_path):
    application();service,team,slots=overview_case(tmp_path)
    with UserDataDao(service.database_path,account_id='overview') as dao:
        with dao.write_transaction() as db:
            db.execute('UPDATE role_loadout_slot SET current_plan_id=NULL WHERE slot_id=?',(slots[team.character_ids[0]],))
            db.execute('DELETE FROM team_overview_binding WHERE team_id=? AND character_id=?',(team.team_id,team.character_ids[1]))
        dao.create_loadout_slot(team.character_ids[1],'备用',slot_key='alternate')
    before=dump(service.database_path);dialog=TeamLoadoutDialog(service=service,team=team,roles={})
    try:
        dialog.open();wait_for(lambda:dialog._expected is not None and not dialog.is_running())
        assert '2/4' in dialog.ready_count.text()
        assert dialog.rows[team.character_ids[0]].status.text()=='空槽位'
        assert dialog.rows[team.character_ids[1]].choice.currentData() is None
        assert dialog.rows[team.character_ids[1]].status.text()=='请选择槽位'
        assert not dialog.buttons.button(QDialogButtonBox.Save).isEnabled()
        dialog.reject();wait_for(lambda:not dialog.is_running())
        assert dump(service.database_path)==before
    finally:drain_dialog(dialog);dispose(dialog)


@pytest.mark.parametrize('theme',["black"])
def test_graduation_and_target_long_patch_keep_footer_and_editor_reachable(tmp_path,monkeypatch,theme):
    from src.app import window_geometry
    app=application();previous=current_theme_name();apply_app_theme(app,theme)
    monkeypatch.setattr(window_geometry,'_available_geometry',lambda _dialog:QRect(0,0,620,800))
    catalog=sample_catalog();service,_inputs,_client,_values,team=case(tmp_path)
    owner=TeamAxisGraduationController(service_provider=lambda:service)
    dialog=TeamAxisGraduationDialog(owner=owner,team=team,catalog=catalog)
    target=None
    try:
        dialog.open();wait_for(lambda:not owner.is_running())
        for button in [dialog.current,dialog.save,dialog.stop]:
            assert not button.visibleRegion().isEmpty()
            assert dialog.rect().contains(button.mapTo(dialog,button.rect().bottomRight()))
        assert dialog.scroll.horizontalScrollBar().maximum()==0
        target=GraduationTargetDialog(configuration=configuration(),catalog=catalog)
        target.open();app.processEvents();app.processEvents()
        for button in target.buttons.buttons():assert target.rect().contains(button.mapTo(target,button.rect().bottomRight()))
        assert target.frameGeometry().right()<620
        for combo in target.layouts.values():assert combo.currentData() is not None
    finally:
        if target is not None:dispose(target)
        cleanup(owner,dialog);apply_app_theme(app,previous)


@pytest.mark.parametrize('width',[520])
def test_calculation_controls_long_patch_reflows_without_overflow_or_write(tmp_path,width):
    from src.features.allocation.team_axis import team_axis_controls as module
    from unittest.mock import patch
    app=application();widget=QWidget();layout=QVBoxLayout(widget)
    owner=SimpleNamespace(text_catalog=sample_catalog(),app_context=SimpleNamespace(account=SimpleNamespace(user_database_path=tmp_path/'unused.sqlite3')))
    with patch.object(module,'load_search_mode',return_value='fast'):
        build_team_axis_controls(owner,layout)
    try:
        battle=SimpleNamespace(battle_record_id=1,start_us=None,end_us=None,detail_scope='first')
        apply_team_axis_selection(owner,TeamAxisSelection((TeamAxisBinding(teams()[0],(),SimpleNamespace(battle=battle,anchor_mode='original')),)))
        widget.resize(width,240);widget.show();app.processEvents();app.processEvents()
        for control in [owner.team_axis_check,owner.team_axis_configure,owner.team_axis_search]:
            assert widget.rect().contains(control.mapTo(widget,control.rect().bottomRight()))
        assert not (tmp_path/'unused.sqlite3').exists()
    finally:dispose(widget)


def test_loadout_footer_keyboard_follows_visible_order_and_cancel_discards_draft(tmp_path):
    from src.domain.teams.overview import SourceMode
    app=application();service,team,_slots=overview_case(tmp_path)
    before=dump(service.database_path)
    dialog=TeamLoadoutDialog(service=service,team=team,roles={})
    try:
        dialog.open();wait_for(lambda:dialog._expected is not None and not dialog.is_running())
        combo=next(iter(dialog.slots.values()));combo.setFocus();QTest.keyClick(combo,Qt.Key_Return)
        assert dialog.selection is None and dialog.isVisible()
        combo.hidePopup()
        dialog.mode_buttons[SourceMode.GAME].click();wait_for(lambda:not dialog.is_running())
        save=dialog.buttons.button(QDialogButtonBox.Save);cancel=dialog.buttons.button(QDialogButtonBox.Cancel)
        assert save.isEnabled()
        save.setFocus();assert app.focusWidget() is save
        QTest.keyClick(save,Qt.Key_Tab);assert app.focusWidget() is cancel
        QTest.keyClick(cancel,Qt.Key_Return);wait_for(lambda:not dialog.is_running())
        assert dialog.selection is None and dump(service.database_path)==before
    finally:drain_dialog(dialog);dispose(dialog)
