# 验证提交通知只在正式成功后发布，定向失效隔离账号且隐藏页不启动后台工作。
from concurrent.futures import CancelledError
from contextlib import nullcontext
from dataclasses import replace
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from src.domain.equipment.loadouts.plan_changes import SavedPlansChanged
from src.domain.teams.models import TeamDraft, TeamRole
from src.features.teams.overview_controller import TeamOverviewController
from src.features.teams.page import TeamPage
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.teams.overview.team_overview_fixture import overview_case
from tests.teams.overview.test_team_overview_ui import wait_for


# 来源、事务与账号失效属于核心边界，显式分层避免仅按文件名遗漏。
NTE_TEST_TIER = "core"


@pytest.mark.parametrize('visible', [True, False])
def test_saved_notification_targets_members_and_ignores_foreign_identity(tmp_path, monkeypatch, visible):
    application()
    service, team, _ = overview_case(tmp_path)
    with UserDataDao(service.database_path) as dao:
        other = dao.save_team(TeamDraft('不受影响', (9999999,)))
    page = TeamPage()
    page.show_teams((team, other), tuple(TeamRole(key, str(key)) for key in team.character_ids))
    controller = TeamOverviewController(service_provider=lambda: service, page=page)
    page.show()
    try:
        controller.refresh((team, other))
        wait_for(lambda: not controller.is_running())
        preserved = controller._results[other.team_id]
        calls, load = [], service.load
        def counted(teams, **kwargs):
            calls.append(tuple(item.team_id for item in teams))
            return load(teams, **kwargs)
        monkeypatch.setattr(service, 'load', counted)
        event = SavedPlansChanged(service.generation, service.account_id, str(service.database_path), (team.character_ids[0],), ())
        for foreign in (replace(event, generation=event.generation+1), replace(event, account_id='other'),
                        replace(event, database_path=str(tmp_path/'other.sqlite3'))):
            controller.plans_saved(foreign)
        assert not calls and controller._results[other.team_id] is preserved
        if not visible:
            page.hide()
        controller.plans_saved(event)
        if visible:
            wait_for(lambda: not controller.is_running())
            assert calls == [(team.team_id,)] and team.team_id in controller._results
        else:
            application().processEvents()
            assert not calls and not controller.is_running() and team.team_id not in controller._results
            assert '待核对' in page._overview_labels[team.team_id]['source'].text()
        assert controller._results[other.team_id] is preserved
    finally:
        controller.deactivate()
        wait_for(lambda: not controller.is_running())
        dispose(page)
        controller.deleteLater()


@pytest.mark.parametrize('outcome', ['committed', 'new_slot', 'render_failure', 'failed', 'cancelled', 'changed_account'])
def test_save_notification_follows_commit_and_precedes_render_only_for_current_account(tmp_path, monkeypatch, outcome):
    import src.features.allocation.execution.runner as runner
    import src.features.allocation.execution.save_workflow as workflow
    from src.services.allocation.inputs.locks import AllocationLockSnapshot
    events, notifications = [], []
    identity = [(3, 'overview', tmp_path/'user.sqlite3')]
    def render(_value):
        events.append('render')
        if outcome == 'render_failure':
            raise RuntimeError('synthetic render failure after commit')
    preview = SimpleNamespace(commit=SimpleNamespace(entries=(SimpleNamespace(team=SimpleNamespace(team_id='saved-team')),)))
    owner = SimpleNamespace(final_plan={'role': {'valid': True, 'team_enhancement_preview': preview}},
        _worker=None, _saving=False, btn_save=None, _cancel_event=Event(), _pending_allocation_snapshot_id=1,
        _pending_allocation_static_identity=(tmp_path/'static.sqlite3', 'fixture', (1, 1)),
        _pending_allocation_static_release=object(), _pending_strat='axis_priority', _pending_blueprint_combo_limit=1,
        _allocation_frozen_comparisons={}, dialog_parent=None,
        _allocation_lock_snapshot=AllocationLockSnapshot(inventory_snapshot_id=1, locked_role_names=frozenset(),
            reserved_uids=frozenset(), plan_revisions=()), _render_results=render)
    monkeypatch.setattr(runner, '_allocation_context_identity', lambda _owner: identity[0])
    monkeypatch.setattr(runner, '_allocation_paths', lambda _owner: (identity[0][2], tmp_path, tmp_path, tmp_path, tmp_path/'static.sqlite3'))
    monkeypatch.setattr(runner, '_select_allocation_save_slots',
                        lambda *args, **kwargs: {'role': (1003, None if outcome == 'new_slot' else 7)})
    monkeypatch.setattr(runner, '_persistable_plan_diff', lambda _diff: {})
    monkeypatch.setattr(runner, '_plan_changed_uids', lambda *args: set())
    monkeypatch.setattr(runner, '_plan_assignment_scores', lambda *args: {})
    monkeypatch.setattr(workflow, 'require_allocation_release', lambda *args: object())
    monkeypatch.setattr(workflow, 'UserDataDao', lambda *args: nullcontext(Mock()))
    reader = Mock()
    reader.read_unit.return_value = nullcontext(SimpleNamespace(dao=Mock()))
    monkeypatch.setattr(workflow, 'StaticCatalogReleaseReader', lambda: reader)
    monkeypatch.setattr(workflow, 'selected_legacy_comparison_diffs', lambda *args: {})
    monkeypatch.setattr(workflow, 'allocation_role_state_from_plan', lambda _plan: {})
    monkeypatch.setattr(workflow, 'legacy_plan_tape_main_values', lambda _plan: {})
    monkeypatch.setattr(workflow, 'invalidate_saved_equipment_cache', lambda _parent: None)
    monkeypatch.setattr(workflow, 'QMessageBox', Mock())
    def persist(**kwargs):
        if outcome == 'failed':
            raise ValueError('synthetic save failure')
        if outcome == 'cancelled':
            raise CancelledError('synthetic cancel')
        events.append('commit')
        return 1
    monkeypatch.setattr(workflow, 'save_allocation_plans', persist)
    class Progress:
        def __init__(self, _parent, **_kwargs):
            pass
        def run(self, task, _owner):
            result = task(lambda _stage: None)
            if outcome == 'changed_account':
                identity[0] = (4, 'other', tmp_path/'other.sqlite3')
            return result
        def set_stage(self, _stage):
            pass
        def close(self):
            pass
        def deleteLater(self):
            pass
    monkeypatch.setattr(workflow, 'AllocationSaveProgress', Progress)
    def notify(event):
        notifications.append(event)
        events.append('notify')
    result = workflow.save_allocation(owner, show_message=False, on_saved=notify)
    committed = outcome in {'committed', 'new_slot', 'render_failure'}
    assert result is committed
    if committed:
        assert events == ['commit', 'notify', 'render']
        assert notifications == [SavedPlansChanged(3, 'overview', str(tmp_path/'user.sqlite3'), (1003,),
            ('saved-team',), () if outcome == 'new_slot' else (7,))]
        assert owner.final_plan['role']['team_enhancement_saved']
    else:
        assert not notifications and 'render' not in events


def test_saved_slots_do_not_invalidate_other_bindings_or_game_equipment(tmp_path, monkeypatch):
    from src.domain.teams.overview import SourceMode
    application()
    service, team, slots = overview_case(tmp_path)
    with UserDataDao(service.database_path) as dao:
        specified = dao.save_team(TeamDraft('同角色其他槽位', team.character_ids))
        game = dao.save_team(TeamDraft('游戏来源', team.character_ids))
        char = team.character_ids[0]
        plan = dao.get_loadout_slot(slots[char])['current_plan']
        alternate = dao.create_loadout_slot(char, '其他槽位')
        dao.save_loadout_plan(name='备用', character_id=char, source_snapshot_id=plan['source_snapshot_id'],
            assignments=plan['assignments'], status='ready', payload=plan['payload'], slot_id=alternate)
        bindings = tuple((key, alternate if key == char else value) for key, value in slots.items())
        dao.save_team_source(specified, dao.get_team_source(specified.team_id), mode=SourceMode.CALC, bindings=bindings)
        dao.save_team_source(game, dao.get_team_source(game.team_id), mode=SourceMode.GAME)
    page = TeamPage()
    page.show_teams((team, specified, game), ())
    controller = TeamOverviewController(service_provider=lambda: service, page=page)
    page.show()
    try:
        controller.refresh((team, specified, game))
        wait_for(lambda: not controller.is_running())
        untouched = {key: controller._results[key] for key in (specified.team_id, game.team_id)}
        calls, load = [], service.load
        def counted(teams, **kwargs):
            calls.append(tuple(item.team_id for item in teams))
            return load(teams, **kwargs)
        monkeypatch.setattr(service, 'load', counted)
        controller.plans_saved(SavedPlansChanged(service.generation, service.account_id, str(service.database_path),
            (char,), (), (slots[char],)))
        wait_for(lambda: not controller.is_running())
        assert calls == [(team.team_id,)]
        assert all(controller._results[key] is value for key, value in untouched.items())
    finally:
        controller.deactivate()
        wait_for(lambda: not controller.is_running())
        dispose(page)
        controller.deleteLater()


def test_real_allocation_owner_forwards_save_event_through_scanning_public_signal(monkeypatch):
    from pathlib import Path
    from PySide6.QtWidgets import QWidget
    from src.features.scanning.controller import ScanningController
    from src.domain.preferences.work_mode import WorkMode
    application()
    parent = QWidget()
    root = Path('unused')
    context = SimpleNamespace(generation=3, account=SimpleNamespace(active_account_id='overview',
        screenshot_dir=root, user_config_dir=root, user_database_path=root/'unused.sqlite3'),
        paths=SimpleNamespace(config_dir=root, template_dir=root, equipment_allocation_database_path=root/'static.sqlite3',
                              equipment_allocation_asset_root=root/'game_ui'))
    controller = ScanningController(global_preferences=Mock(), app_context=context, dialog_parent=parent,
        navigate=Mock(), work_mode_provider=lambda: WorkMode.LOW, minimize_window=Mock(), restore_window=Mock(),
        activate_window=Mock(), update_inventory_status=Mock(), refresh_home=Mock(), preferences_provider=lambda: {},
        save_preferences=Mock(), refresh_roles=Mock(), refresh_equipment=Mock(), card_factory=Mock(),
        equipment_presentation=SimpleNamespace(allocation_plan_diff={}), hotkey_manager=Mock())
    event = SavedPlansChanged(3, 'overview', str(root/'unused.sqlite3'), (1003,), (), (7,))
    observed = []
    controller.allocation_saved.connect(observed.append)
    def saved(owner, *, show_message, on_saved, **_kwargs):
        assert not show_message
        on_saved(event)
        return True
    monkeypatch.setattr('src.features.allocation.controller.save_allocation', saved)
    try:
        assert controller._save_alloc(show_message=False)
        assert observed == [event]
    finally:
        dispose(parent)
