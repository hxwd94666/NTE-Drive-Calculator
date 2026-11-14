# 验证唯一槽位默认来源的只读一致性、显式绑定保护、并发失效及取消不写入。
from dataclasses import replace
import sqlite3

import pytest
from PySide6.QtWidgets import QDialogButtonBox

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from src.domain.teams.models import TeamDraft
from src.domain.teams.overview import SourceMode, TeamSourceConfig
from src.domain.teams.source_selection import with_single_slot_defaults
from src.features.teams.loadouts.loadout_dialog import TeamLoadoutDialog
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.teams.overview.team_overview_fixture import overview_case
from tests.teams.overview.test_team_overview_ui import wait_for


def dump(path):
    with sqlite3.connect(path) as db:
        return tuple(db.iterdump())


@pytest.mark.parametrize('slots,bindings,expected', [
    ([], (), ()),
    ([dict(slot_id=11, character_id=1, current_plan_id=None)], (), ()),
    ([dict(slot_id=11, character_id=1, current_plan_id=1)], (), ((1, 11),)),
    ([dict(slot_id=11, character_id=1, current_plan_id=1),
      dict(slot_id=12, character_id=1, current_plan_id=None)], (), ()),
    ([dict(slot_id=11, character_id=1, current_plan_id=1, is_archived=True)], (), ()),
    ([dict(slot_id=11, character_id=1, current_plan_id=1)], ((1, 99),), ((1, 99),)),
])
def test_only_unambiguous_saved_slot_is_default(slots, bindings, expected):
    config = TeamSourceConfig('t', SourceMode.CALC, 7, bindings)
    result = with_single_slot_defaults(config, slots)
    assert result.bindings == expected and result.revision == 7
    game = replace(config, mode=SourceMode.GAME, bindings=())
    assert with_single_slot_defaults(game, slots) == game


def unbound_case(tmp_path):
    service, original, slots = overview_case(tmp_path)
    with UserDataDao(service.database_path, account_id='overview') as dao:
        team = dao.save_team(TeamDraft('默认来源', original.character_ids))
    return service, team, slots


def test_defaults_match_preview_and_overview_without_persisting(tmp_path):
    service, team, slots = unbound_case(tmp_path)
    before = dump(service.database_path)
    source, _ = service.source_choices(team)
    assert source.bindings == tuple(sorted(slots.items()))
    preview = service.preview_source(team, source, mode=SourceMode.CALC)
    overview, = service.load((team,))
    assert preview.source == source == overview.source
    assert preview.ready_count == overview.ready_count == 4
    # 冗余保存同一有效默认值也不推进修订、不补写隐式绑定。
    assert service.save_source(team, source, mode=SourceMode.CALC) == source
    assert dump(service.database_path) == before


def test_second_visible_slot_invalidates_inferred_selection_and_cas(tmp_path):
    service, team, slots = unbound_case(tmp_path)
    old, _ = service.source_choices(team)
    key = team.character_ids[0]
    with UserDataDao(service.database_path, account_id='overview') as dao:
        dao.create_loadout_slot(key, '第二槽', slot_key='alternate')
    current, _ = service.source_choices(team)
    assert key not in dict(current.bindings)
    with pytest.raises(ValueError, match='变化'):
        service.save_source(team, old, mode=SourceMode.CALC, bindings=old.bindings)
    # 用户原来保存的明确绑定不随新增槽位消失。
    with UserDataDao(service.database_path, account_id='overview') as dao:
        explicit_team = next(t for t in dao.list_teams() if t.team_id != team.team_id)
        assert dict(dao.get_team_source(explicit_team.team_id).bindings)[key] == slots[key]


def test_invalid_explicit_binding_does_not_fall_back(tmp_path):
    service, team, slots = overview_case(tmp_path)
    key = team.character_ids[0]
    with UserDataDao(service.database_path, account_id='overview') as dao:
        replacement = dao.create_loadout_slot(key, '替代槽', slot_key='alternate')
        plan = dao.get_loadout_slot(slots[key])['current_plan']
        dao.save_plan_to_slot(replacement, name='替代方案', assignments=plan['assignments'],
            source_snapshot_id=plan['source_snapshot_id'], status='ready', score=1, payload={})
        assert dao.archive_loadout_slot(slots[key])
    source, _ = service.source_choices(team)
    assert dict(source.bindings)[key] == slots[key]
    result, = service.load((team,))
    assert result.ready_count == 3 and result.members[0].issues


def test_dialog_defaults_are_ready_and_cancel_does_not_write(tmp_path):
    application()
    service, team, slots = unbound_case(tmp_path)
    before = dump(service.database_path)
    dialog = TeamLoadoutDialog(service=service, team=team, roles={})
    try:
        dialog.show()
        wait_for(lambda: dialog._expected is not None and not dialog.is_running())
        assert {key: choice.currentData() for key, choice in dialog.slots.items()} == slots
        assert all(choice.count() == 1 for choice in dialog.slots.values())
        assert '4/4' in dialog.ready_count.text()
        assert not dialog.buttons.button(QDialogButtonBox.Save).isEnabled()
        dialog.reject()
        wait_for(lambda: not dialog.is_running())
        assert dump(service.database_path) == before
    finally:
        dialog.reject()
        wait_for(lambda: not dialog.is_running())
        dispose(dialog)
