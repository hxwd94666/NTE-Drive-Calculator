# 验证轴策略必须明确选择本账号报告、范围、角色与A，关闭或过期不回写任何状态。
from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import replace
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox
import pytest

from tests.battle_report.analysis.axis_evaluation_catalog_fixture import create_axis_catalog_case
from src.features.allocation.team_axis.team_axis_dialog import TeamAxisDialog
from src.domain.teams.models import TeamDraft
from src.services.allocation.axis.axis_selection import AxisAllocationSelectionService
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.teams.matching.team_report_fixture import MatchingFixture
from tests.teams.matching.team_report_fixture import drain_dialog
from tests.teams.matching.team_report_fixture import wait_for


NTE_TEST_TIER = "core"


@pytest.fixture
def selection_service(tmp_path):
    case = create_axis_catalog_case(tmp_path)
    current = {"value": True}
    service = AxisAllocationSelectionService(dependencies=case["dependencies"], context_is_current=lambda _: current["value"])
    choices = service.choices(["灵可", "未映射自建角色"])
    return case, service, choices, current


def test_choices_and_selection_use_formal_participant_and_never_default_a_or_report(selection_service):
    case, service, choices, _ = selection_service
    assert choices.role_character_ids == (("灵可", 1072),)
    for invalid in ({"battle_record_id": None}, {"selected_role": "未映射自建角色"}, {"detail_scope": None}, {"anchor_mode": None}, {"anchor_mode": "effective"}):
        arguments = dict(battle_record_id=case["battle_record_id"], selected_role="灵可", detail_scope="all", anchor_mode="original")
        arguments.update(invalid)
        with pytest.raises(ValueError):
            service.select(choices, **arguments)
    selected = service.select(choices, battle_record_id=case["battle_record_id"], selected_role="灵可", detail_scope="all", anchor_mode="original")
    assert selected.selected_character_id == 1072 and selected.account_id == "axis-evaluation-public"
    assert selected.battle.start_us is None and selected.battle.marginal_candidate is None


def test_range_and_generation_must_remain_frozen(selection_service):
    case, service, choices, current = selection_service
    selected = service.select(choices, battle_record_id=case["battle_record_id"], selected_role="灵可", detail_scope="range",
                              anchor_mode="original", start_us=500_000, end_us=1_000_001)
    assert (selected.battle.start_us, selected.battle.end_us) == (500_000, 1_000_001)
    current["value"] = False
    with pytest.raises(CancelledError):
        service.select(choices, battle_record_id=case["battle_record_id"], selected_role="灵可", detail_scope="all", anchor_mode="original")


def test_unrelated_account_setting_does_not_cancel_explicit_report_selection(selection_service):
    case, service, choices, _ = selection_service
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        dao.replace_application_setting_copy("ui", {"background_marker": "public-fixture"})
    selected = service.select(choices, battle_record_id=case["battle_record_id"], selected_role="灵可",
                              detail_scope="all", anchor_mode="original")
    assert selected.selected_character_id == 1072


def test_group_scope_is_explicit_and_does_not_silently_pick_first_role(selection_service):
    case, service, choices, _ = selection_service
    selected = service.select(choices, battle_record_id=case["battle_record_id"], selected_role="__all_groups__",
                              detail_scope="all", anchor_mode="original")
    assert selected.optimize_groups and selected.selected_role is None and selected.selected_character_id is None
    assert selected.battle.selected_character_id is None


def make_team_dialog(selection_service, **kwargs):
    case, service, choices, _ = selection_service
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        team = dao.save_team(TeamDraft("公开四人队", (1072, 1003, 1004, 1008)))
    choices = replace(choices, role_character_ids=(("灵可", 1072), ("队友甲", 1003), ("队友乙", 1004), ("队友丙", 1008)))
    matcher = MatchingFixture(choices.entries, (team,))
    return TeamAxisDialog(service=service, matching_service=matcher, teams=(team,), choices=choices, **kwargs)


def test_team_dialog_requires_report_and_preserves_explicit_range(selection_service):
    from tests.support.auto_sync_ui_fixture import application
    from tests.support.auto_sync_ui_fixture import dispose
    application()
    case, service, choices, _ = selection_service
    selected = service.select(choices, battle_record_id=case["battle_record_id"], selected_role="灵可", detail_scope="range",
                              anchor_mode="original", start_us=500_000, end_us=1_000_001)
    dialog = make_team_dialog(selection_service, initial_request=replace(selected.battle, marginal_drive_units=(("AtkUp", 100.0),)))
    try:
        row = dialog._rows[0]
        assert row["match"].candidate.range.record_id == case["battle_record_id"]
        assert row["match"].candidate.range.scope == "range"
        dialog.buttons.button(QDialogButtonBox.Ok).click()
        assert dialog.selection is None  # 来自战报的快捷入口不替用户选队伍。
        row["check"].click()
        dialog.buttons.button(QDialogButtonBox.Ok).click()
        wait_for(lambda: not dialog.is_running())
        axis = dialog.selection.teams[0].axis
        assert axis.optimize_groups and axis.battle.marginal_drive_units is None
        assert (axis.battle.start_us, axis.battle.end_us) == (500_000, 1_000_001)
    finally:
        drain_dialog(dialog)
        dispose(dialog)


def test_team_dialog_range_keyboard_cancel_has_no_selection_write(selection_service):
    from tests.support.auto_sync_ui_fixture import application
    from tests.support.auto_sync_ui_fixture import dispose
    app = application()
    case, service, _, _ = selection_service
    dialog = make_team_dialog(selection_service)
    try:
        dialog.show()
        row = dialog._rows[0]
        row["check"].click()
        assert row["match"] is None
        dialog.buttons.button(QDialogButtonBox.Ok).click()
        assert dialog.selection is None
        app.processEvents()
        with patch.object(service, "select", wraps=service.select) as select:
            QTest.keyClick(dialog.buttons.button(QDialogButtonBox.Cancel), Qt.Key_Space)
            select.assert_not_called()
        assert dialog.selection is None and dialog.result() == 0 and not dialog.isVisible()
    finally:
        drain_dialog(dialog)
        dispose(dialog)
