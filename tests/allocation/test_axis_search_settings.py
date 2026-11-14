# 验证搜索档位的账号隔离、界面恢复和双队冻结传递，不执行长时间搜索。
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from src.services.allocation.inputs.search_settings import load_search_mode, save_search_mode, search_budget
from src.storage.sqlite.user_data_dao import UserDataDao


def test_search_profiles_persist_per_account_and_old_settings_default_fast(tmp_path):
    a, b = tmp_path/'a.sqlite3', tmp_path/'b.sqlite3'
    UserDataDao(a, account_id='a').close()
    UserDataDao(b, account_id='b').close()
    assert load_search_mode(a) == load_search_mode(b) == 'fast'
    save_search_mode(a, 'deep')
    assert load_search_mode(a) == 'deep' and load_search_mode(b) == 'fast'
    assert search_budget('fast')['max_millis'] == 120_000
    assert search_budget('deep') == {'max_millis':600_000, 'max_evaluations':100_000}
    with pytest.raises(ValueError):
        save_search_mode(a, 'unlimited')
    assert load_search_mode(a) == 'deep'


def test_ui_restores_each_account_without_overwriting_other_account(tmp_path):
    from tests.support.auto_sync_ui_fixture import application
    from tests.support.auto_sync_ui_fixture import dispose
    from src.features.allocation.team_axis.team_axis_controls import build_team_axis_controls, reload_search_mode
    application()
    a,b = tmp_path/'a.sqlite3',tmp_path/'b.sqlite3'
    UserDataDao(a,account_id='a').close()
    UserDataDao(b,account_id='b').close()
    widget=QWidget()
    owner=SimpleNamespace(app_context=SimpleNamespace(account=SimpleNamespace(user_database_path=a)),dialog_parent=widget)
    layout=QVBoxLayout(widget)
    header=QHBoxLayout()
    layout.addLayout(header)
    build_team_axis_controls(owner,layout)
    try:
        assert owner.team_axis_search.currentData() == 'fast'
        owner.team_axis_search.setCurrentIndex(owner.team_axis_search.findData('deep'))
        assert load_search_mode(a) == 'deep'
        owner.app_context.account.user_database_path=b
        reload_search_mode(owner)
        assert owner.team_axis_search.currentData() == 'fast'
        assert load_search_mode(a) == 'deep'
    finally:
        dispose(widget)


def test_deep_profile_is_frozen_for_both_teams_but_not_remaining_roles(tmp_path):
    from tests.allocation.test_team_axis_enhancement import selection_for_database
    from src.services.allocation.axis.team_enhancement import execute_team_enhancement
    database,selection=selection_for_database(tmp_path)
    facade=SimpleNamespace(user_database_path=database,execute_allocation_inventory=Mock(return_value=({},None)))
    execute_team_enhancement(facade,[],list(selection.roles)+['remaining'],replace(selection,search_mode='deep'),
                             mode='role_priority',options={})
    calls=facade.execute_allocation_inventory.call_args_list
    assert all(axis.search_mode=='deep' for axis,_ in calls[0].kwargs['axis_selection'])
    assert 'axis_selection' not in calls[1].kwargs


def test_wire_accepts_deep_axis_budget_without_relaxing_direct_search():
    from tests.allocation.test_group_postprocess_wire import group_request
    from src.integrations.allocation.allocation_group_postprocess import _request
    from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError
    axis = group_request(axis=True)
    axis['budget']['max_millis'] = 600_000
    _request(axis, axis=True)
    axis['budget']['max_millis'] = 600_001
    with pytest.raises(NativeAnalysisError):
        _request(axis, axis=True)
    direct = group_request(axis=False)
    direct['budget']['max_millis'] = 600_000
    with pytest.raises(NativeAnalysisError):
        _request(direct, axis=False)
