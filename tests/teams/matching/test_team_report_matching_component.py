# 使用随附只读分析组件核验合成战报的完整阵容，不连接游戏或个人账号。
from pathlib import Path
from dataclasses import replace

from src.domain.teams.models import TeamDraft
from src.domain.teams.report_matching import MatchStatus
from src.services.teams.report_matching import TeamReportMatchingService
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.allocation.axis_team_fixture import attach_native_team
from tests.battle_report.analysis.axis_evaluation_catalog_fixture import create_axis_catalog_case


def test_bundled_component_matches_all_four_including_zero_damage_members_and_rechecks_cache(tmp_path):
    case = create_axis_catalog_case(tmp_path)
    attach_native_team(case)
    deps = case["dependencies"]
    with UserDataDao(deps.user_database_path) as dao:
        exact = dao.save_team(TeamDraft("正式四人", (1008, 1004, 1003, 1072)))
        different = dao.save_team(TeamDraft("三人相同", (1008, 1004, 1003, 1051)))
        before = tuple(dao._db().iterdump())
    service = TeamReportMatchingService(dependencies=replace(deps, generation=0), context_is_current=lambda _: True)
    candidates = service.discover().candidates
    result = service.match_page(exact, candidates).results[0]
    assert result.status == MatchStatus.MATCHED, result.reason
    assert set(result.report_character_ids) == {1072, 1003, 1004, 1008}
    assert result.source_input_digest and result.input_identity
    assert service.match_page(exact, candidates).results[0] == result
    other = service.match_page(different, candidates).results[0]
    assert other.status == MatchStatus.DIFFERENT, other.reason
    assert len(other.report_character_ids) == 4
    with UserDataDao(deps.user_database_path) as dao:
        assert tuple(dao._db().iterdump()) == before
    assert Path(deps.user_database_path).is_file()
    # 同一战报正式输入改变后，旧缓存不再称为完全匹配。
    attach_native_team(case, (1072, 1003, 1004, 1051))
    changed = service.match_page(exact, candidates).results[0]
    assert changed.status == MatchStatus.DIFFERENT
    assert changed.source_input_digest != result.source_input_digest


def test_team_graduation_composition_only_updates_axis_after_explicit_confirmation(tmp_path):
    from PySide6.QtWidgets import QDialogButtonBox, QWidget
    from tests.support.auto_sync_ui_fixture import application
    from tests.support.auto_sync_ui_fixture import dispose
    from tests.teams.matching.team_report_fixture import wait_for
    from src.app.context import AccountContext, AppContext, ApplicationPaths
    from src.ui.composition.team_composition import build_team_controller
    from tests.battle_report.analysis.axis_evaluation_catalog_fixture import ROOT
    application()
    case = create_axis_catalog_case(tmp_path)
    attach_native_team(case)
    deps = case["dependencies"]
    with UserDataDao(deps.user_database_path) as dao:
        team = dao.save_team(TeamDraft("组合根匹配队伍", (1072, 1003, 1004, 1008)))
    paths = ApplicationPaths.from_roots(root=ROOT, app_dir=ROOT, data_root=tmp_path,
        bundled_config_dir=ROOT / "config", asset_dir=ROOT / "assets", app_icon_path=ROOT / "assets/app_icon.ico")
    account = AccountContext(deps.account_id, "合成账号", tmp_path, deps.user_database_path,
                             tmp_path / "config", tmp_path / "screenshots", tmp_path / "logs")
    context = AppContext(paths, account)
    parent = QWidget()
    controller = build_team_controller(context, parent)
    try:
        assert controller.refresh()
        parent.show()
        controller.page.show()
        controller.open_graduation(team.team_id)
        wait_for(lambda: not controller.axis_graduation.is_running(), timeout=15)
        graduation = controller._graduation_dialog
        graduation.axis_requested.emit()
        dialog = controller._match_dialog
        dialog.service.checkpoint()
        errors = []
        dialog._controller.error.connect(errors.append)
        try:
            wait_for(lambda: (dialog._results or errors) and not dialog.is_running(), timeout=15)
        except AssertionError as error:
            raise AssertionError((dialog.status.text(), dialog.is_running(), dialog.isVisible(), errors)) from error
        assert dialog.list.topLevelItemCount() == 1, (dialog.status.text(), tuple(result.reason for result in dialog._results.values()))
        assert graduation.reference_axis is None
        dialog.list.setCurrentItem(dialog.list.topLevelItem(0))
        dialog.buttons.button(QDialogButtonBox.Ok).click()
        wait_for(lambda: controller._match_dialog is None, timeout=15)
        assert graduation.reference_axis == dialog.selection.candidate.range
        assert graduation.reference_axis.scope == "all"
    finally:
        controller.deactivate()
        wait_for(lambda: not controller.is_running())
        dispose(parent)


def test_axis_dialog_async_discovery_and_confirmation_use_bundled_team_checker(tmp_path):
    from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox
    from tests.support.auto_sync_ui_fixture import application
    from tests.support.auto_sync_ui_fixture import dispose
    from tests.teams.matching.team_report_fixture import drain_dialog
    from tests.teams.matching.team_report_fixture import wait_for
    from src.features.allocation.team_axis.team_axis_dialog import TeamAxisDialog
    from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
    from src.services.allocation.axis.axis_selection import AxisAllocationSelectionService
    from src.services.battle_report.analysis.load import BattleReportAnalysisLoadRequest
    application()
    case = create_axis_catalog_case(tmp_path)
    attach_native_team(case)
    deps = case["dependencies"]
    with UserDataDao(deps.user_database_path) as dao:
        team = dao.save_team(TeamDraft("完整增强入口", (1072, 1003, 1004, 1008)))
        before = tuple(dao._db().iterdump())
    with static_catalog_read_unit(deps.static_database_path) as unit:
        names = tuple(row["name_zh"] for row in unit.dao.list_characters() if row["character_id"] in team.character_ids)
    service = AxisAllocationSelectionService(dependencies=deps, context_is_current=lambda _: True)
    matcher = TeamReportMatchingService(dependencies=deps, context_is_current=lambda _: True)
    dialog = TeamAxisDialog(service=service, matching_service=matcher, role_names=names,
                            initial_request=BattleReportAnalysisLoadRequest(case["battle_record_id"]))
    try:
        dialog.show()
        wait_for(lambda: dialog._rows and dialog._rows[0]["match"] is not None
                 and dialog._rows[0]["match"].status == MatchStatus.MATCHED and not dialog.is_running(), timeout=15)
        assert not any(box.isChecked() for box in dialog.findChildren(QCheckBox))
        next(box for box in dialog.findChildren(QCheckBox) if box.accessibleName() == f"选择队伍 {team.name}").click()
        dialog.buttons.button(QDialogButtonBox.Ok).click()
        wait_for(lambda: not dialog.is_running(), timeout=15)
        assert dialog.result() == QDialog.Accepted, dialog.error.text()
        binding, = dialog.selection.teams
        assert binding.team == team and binding.axis.expected_source_input_digest
        with UserDataDao(deps.user_database_path) as dao:
            assert tuple(dao._db().iterdump()) == before
    finally:
        drain_dialog(dialog)
        dispose(dialog)
