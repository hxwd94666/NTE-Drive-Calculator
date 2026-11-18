# 验证最近结果不追求最大收益、历史关联失效不重算、账号包格式和只读详情收尾。
from dataclasses import replace
import json

import pytest

from src.domain.teams.enhancement_presentation import team_enhancement_presentation
from src.domain.teams.models import TeamDraft
from src.domain.teams.overview import SourceMode
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.teams.enhancement.test_team_enhancement_persistence import commit_for
from tests.teams.enhancement.test_team_enhancement_persistence import payload
from tests.teams.enhancement.test_team_enhancement_persistence import prepared
from tests.teams.overview.team_overview_fixture import overview_case


# 来源、事务与账号失效属于核心边界，显式分层避免仅按文件名遗漏。
NTE_TEST_TIER = "core"


def save_result(service, team, slots, *, before=100, after=105, kind="optimized"):
    with UserDataDao(service.database_path) as dao:
        value = payload(team, dao.get_team_source(team.team_id), before=before, after=after, kind=kind)
        request = commit_for(dao, team, value=value)
        dao.save_calculated_loadout_plans(prepared(dao, slots), checkpoint=lambda: None, team_results=request)
        return request


def test_latest_is_committed_time_not_highest_gain_and_rename_keeps_history(tmp_path):
    service, team, slots = overview_case(tmp_path)
    save_result(service, team, slots, after=130)
    last = save_result(service, team, slots, after=95)
    current, = service.load()
    assert current.latest_enhancement.run_id == last.run_id
    assert current.latest_enhancement.payload["relative_gain"] == pytest.approx(-.05)
    assert current.latest_enhancement.association[0] == "对应当前所选计算配装"
    with UserDataDao(service.database_path) as dao:
        renamed = dao.save_team(TeamDraft("只改名", team.character_ids), existing=team)
    renamed_view, = service.load()
    assert renamed_view.team == renamed and renamed_view.latest_enhancement.run_id == last.run_id


def test_source_member_and_slot_changes_preserve_frozen_values(tmp_path):
    service, team, slots = overview_case(tmp_path)
    save_result(service, team, slots)
    before, = service.load()
    frozen = before.latest_enhancement.payload_json
    with UserDataDao(service.database_path) as dao:
        dao.save_team_source(team, dao.get_team_source(team.team_id), mode=SourceMode.GAME)
        dao.deactivate_loadout_plan(before.latest_enhancement.members[0]["plan_id"])
    after, = service.load()
    assert after.latest_enhancement.payload_json == frozen
    assert "简报来源已改变" in after.latest_enhancement.association
    assert after.latest_enhancement.association[0] == "对应历史保存方案"
    with UserDataDao(service.database_path) as dao:
        changed = dao.save_team(TeamDraft("新阵容", (1, 2, 3, 4)), existing=team)
    view, = service.load()
    assert view.latest_enhancement is None
    history, next_offset = service.enhancement_history(changed)
    assert next_offset is None and history[0].payload_json == frozen
    assert "对应历史阵容" in history[0].association


def test_baseline_coverage_and_blueprint_cut_have_shared_plain_explanations(tmp_path):
    service, team, _ = overview_case(tmp_path)
    with UserDataDao(service.database_path) as dao:
        value = payload(team, dao.get_team_source(team.team_id), before=100, after=98)
    value["coverage"] = dict(policy="available_fixed_domain_v1", basis_damage=100,
        quantified_damage=50, estimated_damage=20, excluded_damage=30)
    value["search"]["blueprint_enumeration_complete"] = False
    shown = team_enhancement_presentation(value)
    assert "-2.00%" in shown.headline and "70.0%" in shown.coverage and "图纸枚举已截断" in shown.search
    baseline = value | dict(result_kind="baseline", baseline_value=None, candidate_value=None, relative_gain=None)
    assert "基础配装已保存" in team_enhancement_presentation(baseline).headline
    assert "0%" not in team_enhancement_presentation(baseline).headline


def test_account_database_roundtrip_preserves_results_and_rejects_bad_body(tmp_path):
    from src.storage.sqlite.accounts.transfer_database import backup_account_database, prepare_account_database
    service, team, slots = overview_case(tmp_path)
    save_result(service, team, slots)
    with UserDataDao(service.database_path) as dao:
        name = dao.profile()["account_name"]
        original = dao.list_team_enhancements(team.team_id)
    copy = tmp_path / "roundtrip.sqlite3"
    backup_account_database(service.database_path, copy)
    prepare_account_database(copy, account_id="overview", account_name=name)
    with UserDataDao(copy) as dao:
        assert dao.list_team_enhancements(team.team_id) == original
        dao.validate_team_sources()
        malformed = original[0].payload | {"relative_gain": 999}
        dao._db().execute("UPDATE team_enhancement_result SET payload_json=?", (json.dumps(malformed),))
        dao._db().commit()
    with pytest.raises(ValueError):
        prepare_account_database(copy, account_id="overview", account_name=name)
    with UserDataDao(service.database_path) as dao:
        assert dao.list_team_enhancements(team.team_id) == original


@pytest.mark.parametrize("theme", ["black"])
def test_history_dialog_is_plain_read_only_and_closes_its_owner(tmp_path, theme):
    from tests.support.auto_sync_ui_fixture import application
    from tests.support.auto_sync_ui_fixture import dispose
    from PySide6.QtCore import QRect, QSize, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialogButtonBox
    from src.app.theme import apply_app_theme
    from src.app.window_geometry import fit_dialog_to_available_screen
    from src.features.teams.enhancement_history_dialog import TeamEnhancementHistoryDialog
    from tests.teams.overview.test_team_overview_ui import wait_for
    application()
    apply_app_theme(application(), theme)
    service, team, slots = overview_case(tmp_path)
    save_result(service, team, slots)
    team = replace(team, name="<b>纯文本队伍</b>")
    with UserDataDao(service.database_path) as dao:
        team = dao.save_team(TeamDraft(team.name, team.character_ids), existing=replace(team, name="四人队"))
    dialog = TeamEnhancementHistoryDialog(service=service, team=team)
    try:
        fit_dialog_to_available_screen(dialog, QSize(800, 730), available_geometry=QRect(0, 0, 640, 600))
        dialog.show()
        wait_for(lambda: not dialog.is_running() and bool(dialog._rows))
        assert dialog.details.isReadOnly() and "优化前计分" in dialog.details.toPlainText() and "优化后计分" in dialog.details.toPlainText()
        assert dialog.sources.isReadOnly() and "静态来源" in dialog.technical.text()
        assert dialog.coverage.isReadOnly() and dialog.association.isReadOnly()
        close = dialog.buttons.button(QDialogButtonBox.Close)
        assert dialog.rect().contains(close.mapTo(dialog, close.rect().bottomRight()))
        QTest.keyClick(dialog, Qt.Key_Escape)
        wait_for(lambda: not dialog.is_running())
    finally:
        dialog.reject()
        wait_for(lambda: not dialog.is_running())
        dispose(dialog)
