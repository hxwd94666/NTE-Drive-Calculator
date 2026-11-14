# 验证准备续接的放弃通知、单队配置预选和语言补丁，不启动求解或游戏输入。
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from src.domain.teams.models import Team
from src.features.allocation.team_axis.team_axis_dialog import TeamAxisDialog
from tests.allocation.test_allocation_catalog_async import page as page


@pytest.mark.parametrize('reason', ['failure', 'leave', 'account', 'busy', 'invalidate', 'close'])
def test_catalog_preparation_releases_abandoned_request_without_calling_ready(page, monkeypatch, reason):
    window, owner, _applied, running = page
    requests, ready, abandoned = [], [], []
    monkeypatch.setattr(owner.lane, 'submit', requests.append)
    owner.refresh(continuation=lambda: ready.append(1), abandoned=lambda: abandoned.append(1))
    request = requests[0]
    if reason == 'failure':
        request.failed('synthetic preparation failure')
    elif reason == 'leave':
        window.stack = SimpleNamespace(currentIndex=lambda: 1)
        window._nav_key_for_index = lambda _: 'home'
        request.apply(('loaded', 'semantic', True, None))
    elif reason == 'account':
        window.app_context.generation += 1
        request.apply(('loaded', 'semantic', True, None))
    elif reason == 'busy':
        running[0] = True
        request.apply(('loaded', 'semantic', True, None))
    elif reason == 'invalidate':
        owner.invalidate()
    else:
        owner.close()
    assert abandoned == [1] and not ready
    assert owner._continuation is None


def test_second_preparation_is_released_without_replacing_first_callback(page, monkeypatch):
    _window, owner, _applied, _running = page
    requests, ready, abandoned = [], [], []
    monkeypatch.setattr(owner.lane, 'submit', requests.append)
    owner.refresh(continuation=lambda: ready.append('first'))
    owner.refresh(continuation=lambda: ready.append('second'), abandoned=lambda: abandoned.append('second'))
    assert abandoned == ['second'] and len(requests) == 1
    requests[0].apply(('loaded', 'semantic', True, None))
    assert ready == ['first']


def test_actual_navigation_revokes_shortcut_before_switching_out_of_calculation():
    from unittest.mock import Mock
    from src.ui.main_window.main_window_navigation_mixin import MainWindowNavigationMixin
    from src.ui.main_window.navigation import nav_index_map
    window = SimpleNamespace(
        stack=SimpleNamespace(currentIndex=lambda: nav_index_map()['execute'], setCurrentIndex=Mock()),
        _nav_key_for_index=lambda _: 'execute', topbar_title=Mock(), topbar_team_notice=Mock(), topbar_catalog_return=Mock(),
        topbar_source_label=Mock(), topbar_equipment_modes=Mock(), _nav_buttons={},
        _refresh_navigation_item=Mock(), scanning_controller=SimpleNamespace(cancel_team_calculation=Mock()))
    MainWindowNavigationMixin._go(window, 'home')
    window.scanning_controller.cancel_team_calculation.assert_called_once()
    window.stack.setCurrentIndex.assert_called_once_with(nav_index_map()['home'])


@pytest.mark.parametrize('changed', [False, True])
def test_shortcut_dialog_preselects_only_exact_source_team_and_requires_report(changed):
    application()
    team = Team('one', '本队', (1, 2, 3, 4), 1)
    other = Team('other', '旧第二队', (5, 6, 7, 8), 1)
    choices = SimpleNamespace(role_character_ids=tuple((f'角色{key}', key) for key in range(1, 9)))
    current = replace(team, revision=2) if changed else team
    dialog = TeamAxisDialog(service=None, matching_service=None, teams=(current, other), choices=choices,
                            required_team=team)
    try:
        dialog.show()
        application().processEvents()
        assert len(dialog._rows) == (0 if changed else 1)
        if not changed:
            assert dialog._rows[0]['team'] == team and dialog._rows[0]['check'].isChecked()
            assert dialog._rows[0]['match'] is None
            dialog.buttons.button(dialog.buttons.StandardButton.Ok).click()
            assert dialog.selection is None and dialog.isVisible()
        else:
            assert '队伍已变化' in dialog.error.text()
    finally:
        dialog.reject()
        dispose(dialog)
