# 用真实选人控件和隔离账号验证单队准备、同级提交、草稿取消及旧预览保存失效。
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QButtonGroup, QCheckBox, QLabel, QPushButton, QRadioButton, QWidget

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from src.domain.teams.calculation import TeamCalculationError, resolve_team_roles
from src.domain.teams.models import TeamDraft
from src.features.allocation.controller import AllocationController
from src.features.allocation.selection.role_selector import RoleSelector
from src.features.scanning import team_calculation as module
from src.services.allocation.axis.team_axis_selection import TeamAxisBinding, TeamAxisSelection
from src.services.teams.calculation import TeamCalculationService
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.teams.test_team_management import service as team_service


@pytest.fixture
def case(tmp_path, monkeypatch):
    application()
    current = [True]
    teams = team_service(tmp_path, current=lambda: current[0])
    team = teams.save(TeamDraft('单队', (4, 2, 3, 1)))
    window = QWidget()
    selector = RoleSelector(window, priority_config_path_provider=lambda: tmp_path/'priority.json')
    selector.load_roles({f'角色{key}': {'character_id': key, 'weights': {}, 'default_set': '测试'}
                         for key in range(1, 7)}, ['测试'])
    selector.restore_priority_selection((('角色5', '角色6'), ('>',)))
    selector.custom_sets = {'角色4': '保留套装', '角色5': '另一个套装'}
    selector.stat_priority_configs = {'角色4': {'blacklist': ['环合强度']}}
    selector.custom_weapons = {'角色4': '已有弧盘'}
    selector.tape_main_filters = {'角色4': ['暴击伤害']}
    selector.crit_rate_caps = {'角色4': 80.0}
    context = SimpleNamespace(generation=2, account=SimpleNamespace(active_account_id='one', user_database_path=teams.database_path))
    allocation = AllocationController(app_context=context, dialog_parent=window, equipment_presentation=Mock(),
        preferences_provider=lambda: {}, save_preferences=Mock(), refresh_roles=Mock(), refresh_equipment=Mock(),
        hotkey_manager=Mock())
    allocation.final_plan = {'old': {'valid': True}}
    save = QPushButton(window)
    save.setEnabled(True)
    allocation.bind_save_button(save)
    page = ['teams']
    callbacks, notices = [], []
    owner = SimpleNamespace(app_context=context, text_catalog=None, dialog_parent=window, role_selector=selector,
        is_running=Mock(return_value=False), _team_axis_dialog=None, build_page=Mock(),
        navigate=Mock(side_effect=lambda key: page.__setitem__(0, key)),
        _prepare_calculation=lambda ready, *, abandoned: callbacks.append((ready, abandoned)),
        team_axis_selection=object(), team_axis_check=QCheckBox(window), team_axis_configure=QPushButton(window),
        team_axis_search=QPushButton(window), team_axis_search_label=QLabel(window))
    owner.scan_group = QButtonGroup(window)
    for key in (1, 4):
        owner.scan_group.addButton(QRadioButton(window), key)
    owner.scan_group.button(1).setChecked(True)
    clear = Mock(wraps=allocation.clear_preview)
    preparing = [False]
    controller = module.TeamCalculationController(owner, clear_preview=clear, current_page=lambda: page[0],
        preparation_busy=lambda: preparing[0], parent=window)
    monkeypatch.setattr(controller, '_notice', lambda code, ids=(): notices.append((code, ids)))
    names = tuple(f'角色{key}' for key in team.character_ids)
    selection = TeamAxisSelection((TeamAxisBinding(team, names, SimpleNamespace(account_id='one', generation=2,
        user_database_path=teams.database_path)),))
    chooser = Mock(return_value=selection)
    monkeypatch.setattr(module, 'choose_team_axes', chooser)
    frozen_preferences = deepcopy((selector.custom_sets, selector.stat_priority_configs, selector.custom_weapons,
                                  selector.tape_main_filters, selector.crit_rate_caps))
    result = SimpleNamespace(owner=owner, team=team, teams=teams, service=TeamCalculationService(teams,generation=2),
        controller=controller, callbacks=callbacks, notices=notices, chooser=chooser, selection=selection,
        allocation=allocation, save=save, clear=clear, page=page, current=current, preferences=frozen_preferences,
        preparing=preparing)
    yield result
    assert frozen_preferences == (selector.custom_sets, selector.stat_priority_configs, selector.custom_weapons,
                                  selector.tape_main_filters, selector.crit_rate_caps)
    assert not (tmp_path/'priority.json').exists()
    controller.cancel()
    dispose(window)


def test_confirm_replaces_only_four_members_equal_and_invalidates_old_preview_without_execution(case):
    c = case
    with UserDataDao(c.teams.database_path) as dao:
        before = tuple(dao._db().iterdump())
    c.controller.start(c.team, c.service)
    assert c.owner.role_selector.get_selected() == ['角色5', '角色6']
    assert c.allocation.final_plan and c.save.isEnabled()
    c.callbacks[0][0]()
    assert c.owner.role_selector.get_selected() == ['角色4', '角色2', '角色3', '角色1']
    assert c.owner.role_selector.priority_links == ['=', '=', '=']
    assert c.owner.role_selector.get_priority_groups() == [['角色4', '角色2', '角色3', '角色1']]
    assert c.owner.team_axis_selection is c.selection and c.owner.team_axis_check.isChecked()
    assert c.owner.team_axis_configure.isEnabled()
    assert not c.allocation.final_plan and not c.save.isEnabled()
    c.clear.assert_called_once()
    c.chooser.assert_called_once_with(c.owner, initial_team_id=c.team.team_id, required_team=c.team)
    assert not c.notices
    assert c.owner.scan_group.checkedId() == 4
    assert c.allocation._worker is None and not c.allocation.is_running()
    with UserDataDao(c.teams.database_path) as dao:
        assert tuple(dao._db().iterdump()) == before


def test_cancel_keeps_original_selection_enhancement_and_preview(case):
    c = case
    old = c.owner.team_axis_selection
    c.chooser.return_value = None
    c.controller.start(c.team, c.service)
    c.callbacks[0][0]()
    assert c.owner.role_selector.get_selected() == ['角色5', '角色6']
    assert c.owner.team_axis_selection is old and not c.owner.team_axis_check.isChecked()
    assert c.allocation.final_plan and c.save.isEnabled()
    assert c.owner.scan_group.checkedId() == 1
    c.clear.assert_not_called()


@pytest.mark.parametrize('change', ['account', 'leave', 'stop', 'draft', 'enhancement', 'team'])
def test_prepare_rejects_stale_callback_without_restoring_over_new_input(case, change):
    c = case
    c.controller.start(c.team, c.service)
    if change == 'account':
        c.owner.app_context.generation += 1
    elif change == 'leave':
        c.page[0] = 'home'
    elif change == 'stop':
        c.controller.cancel()
    elif change == 'draft':
        c.owner.role_selector.restore_priority_selection((('角色6',), ()))
    elif change == 'enhancement':
        c.owner.team_axis_selection = object()
    elif change == 'team':
        c.teams.save(TeamDraft('改名', c.team.character_ids), existing=c.team)
    c.callbacks[0][0]()
    c.chooser.assert_not_called()
    c.clear.assert_not_called()
    assert c.allocation.final_plan and c.save.isEnabled()
    assert c.owner.role_selector.get_selected() == (['角色6'] if change == 'draft' else ['角色5', '角色6'])


@pytest.mark.parametrize('change', ['account', 'draft', 'team', 'foreign_selection'])
def test_confirmation_rechecks_identity_and_does_not_apply_late_choice(case, change):
    c = case
    def choose(*args, **kwargs):
        if change == 'account':
            c.owner.app_context.generation += 1
        elif change == 'draft':
            c.owner.role_selector.restore_priority_selection((('角色6',), ()))
        elif change == 'team':
            c.teams.save(TeamDraft('修订', c.team.character_ids), existing=c.team)
        else:
            return replace(c.selection, teams=(replace(c.selection.teams[0],
                team=replace(c.team, team_id='another')),))
        return c.selection
    c.chooser.side_effect = choose
    c.controller.start(c.team, c.service)
    c.callbacks[0][0]()
    c.clear.assert_not_called()
    assert c.allocation.final_plan and c.save.isEnabled() and not c.owner.team_axis_check.isChecked()


def test_duplicate_start_and_abandoned_catalog_release_pending_request(case):
    c = case
    c.controller.start(c.team, c.service)
    c.controller.start(c.team, c.service)
    assert len(c.callbacks) == 1 and c.notices == [('busy', ())]
    c.callbacks[0][1]()
    c.controller.start(c.team, c.service)
    assert len(c.callbacks) == 2
    c.callbacks[0][0]()
    c.chooser.assert_not_called()
    c.callbacks[1][0]()
    c.clear.assert_called_once()


def test_failed_widget_application_restores_draft_without_invalidating_preview(case, monkeypatch):
    c = case
    old = c.owner.team_axis_selection
    def broken(owner, selection):
        owner.team_axis_selection = selection
        owner.team_axis_check.setChecked(True)
        raise RuntimeError('synthetic widget failure')
    monkeypatch.setattr(module, 'apply_team_axis_selection', broken)
    c.controller.start(c.team, c.service)
    c.callbacks[0][0]()
    assert c.owner.team_axis_selection is old and not c.owner.team_axis_check.isChecked()
    assert c.owner.role_selector.get_selected() == ['角色5', '角色6']
    assert c.allocation.final_plan and c.save.isEnabled()
    c.clear.assert_not_called()


def test_incomplete_or_missing_catalog_never_changes_draft(case):
    c = case
    c.controller.start(replace(c.team, character_ids=(1, 2, 3)), c.service)
    assert c.notices == [('incomplete', ())] and not c.callbacks
    c.owner.role_selector.all_roles.pop('角色4')
    c.controller.start(c.team, c.service)
    c.callbacks[0][0]()
    assert c.notices[-1] == ('catalog_missing', (4,))
    c.clear.assert_not_called()


def test_identity_resolution_rejects_ambiguous_custom_and_nonformal_ids():
    for catalog in ({'a': {'character_id': 1}, 'alias': {'character_id': 1}},
                    {'a': {'character_id': 1, 'is_custom': True}}, {'a': {'character_id': True}}):
        with pytest.raises(TeamCalculationError):
            resolve_team_roles((1, 2, 3, 4), catalog)


@pytest.mark.parametrize('preparing', [False, True])
def test_busy_calculation_does_not_navigate_or_freeze_new_request(case, preparing):
    c = case
    c.owner.is_running.return_value = not preparing
    c.preparing[0] = preparing
    c.controller.start(c.team, c.service)
    c.owner.navigate.assert_not_called()
    assert not c.callbacks and c.notices == [('busy', ())]


def test_team_page_entry_passes_the_original_team_and_frozen_service(case):
    from src.features.teams.controller import TeamController
    c = case
    requested = Mock()
    root = TeamController(service_provider=lambda: c.teams,
        calculation_service_provider=lambda: c.service, calculate_team=requested)
    try:
        assert root.refresh()
        root.page.resize(1200, 500)
        root.page.show()
        application().processEvents()
        button = next(b for b in root.page.findChildren(QPushButton)
                      if b.accessibleName().endswith('：单独计算'))
        button.click()
        requested.assert_called_once_with(c.team, c.service)
    finally:
        root.deactivate()
        dispose(root.page)
        root.deleteLater()


def test_actual_single_team_dialog_confirms_through_bundled_analysis_without_solving(tmp_path):
    from time import monotonic
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialogButtonBox
    from src.domain.teams.models import TeamRole
    from src.domain.teams.report_matching import MatchStatus
    from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
    from src.services.allocation.axis.axis_selection import AxisAllocationSelectionService
    from src.services.teams.management import TeamService
    from src.services.teams.report_matching import TeamReportMatchingService
    from tests.allocation.axis_team_fixture import attach_native_team
    from tests.battle_report.analysis.axis_evaluation_catalog_fixture import create_axis_catalog_case
    application()
    fixture = create_axis_catalog_case(tmp_path)
    attach_native_team(fixture)
    deps = fixture['dependencies']
    ids = (1072, 1003, 1004, 1008)
    with static_catalog_read_unit(deps.static_database_path) as unit:
        names = {row['character_id']: row['name_zh'] for row in unit.dao.list_characters() if row['character_id'] in ids}
    teams = TeamService(account_id=deps.account_id, database_path=deps.user_database_path,
        static_database_path=deps.static_database_path, asset_root=None, context_is_current=lambda: True,
        role_loader=lambda: tuple(TeamRole(key, names[key]) for key in ids))
    team = teams.save(TeamDraft('真实单队配置链', ids))
    window = QWidget()
    selector = RoleSelector(window, priority_config_path_provider=lambda: tmp_path/'priority.json')
    selector.load_roles({names[key]: {'character_id': key, 'weights': {}, 'default_set': '测试'} for key in ids}, ['测试'])
    matcher = TeamReportMatchingService(dependencies=deps, context_is_current=lambda _: True)
    owner = SimpleNamespace(text_catalog=None, dialog_parent=window, role_selector=selector,
        app_context=SimpleNamespace(generation=deps.generation,
            account=SimpleNamespace(active_account_id=deps.account_id, user_database_path=deps.user_database_path)),
        _team_axis_dialog=None, build_page=Mock(), navigate=Mock(), _prepare_calculation=None,
        team_axis_selection=None, team_axis_check=QCheckBox(window), team_axis_configure=QPushButton(window),
        team_axis_search=QPushButton(window), team_axis_search_label=QLabel(window),
        _axis_selection_service_provider=lambda: AxisAllocationSelectionService(dependencies=deps, context_is_current=lambda _: True),
        _team_report_service_provider=lambda: matcher, scan_group=QButtonGroup(window))
    owner.scan_group.addButton(QRadioButton(window), 4)
    owner.is_running = lambda: owner._team_axis_dialog is not None and owner._team_axis_dialog.is_running()
    clear, errors = Mock(), []
    controller = module.TeamCalculationController(owner, clear_preview=clear, current_page=lambda: 'execute', parent=window)
    deadline = monotonic() + 20
    def drive():
        dialog = owner._team_axis_dialog
        try:
            assert monotonic() < deadline, 'single-team dialog did not become ready'
            if dialog is None or dialog.is_running() or not dialog._rows:
                QTimer.singleShot(20, drive)
                return
            assert len(dialog._rows) == 1 and dialog._rows[0]['check'].isChecked()
            result = matcher.match_page(team, matcher.discover().candidates).results[0]
            assert result.status == MatchStatus.MATCHED, result.reason
            dialog.set_report(team.team_id, result)
            dialog.buttons.button(QDialogButtonBox.Ok).click()
        except Exception as error:
            errors.append(error)
            if dialog is not None:
                dialog.reject()
    with UserDataDao(deps.user_database_path) as dao:
        before = tuple(dao._db().iterdump())
    QTimer.singleShot(20, drive)
    try:
        controller.start(team, TeamCalculationService(teams, generation=deps.generation))
        assert not errors, errors
        assert selector.get_priority_groups() == [[names[key] for key in ids]]
        assert owner.team_axis_check.isChecked() and len(owner.team_axis_selection.teams) == 1
        assert owner.team_axis_selection.teams[0].team == team
        clear.assert_called_once()
        with UserDataDao(deps.user_database_path) as dao:
            assert tuple(dao._db().iterdump()) == before
    finally:
        controller.cancel()
        dispose(window)
