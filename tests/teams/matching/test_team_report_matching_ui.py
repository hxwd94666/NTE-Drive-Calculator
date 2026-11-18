# 验证共用匹配列表、原轴时段、显式请求优先、末次任务及窗口退场。
from dataclasses import replace
from threading import Event

from PySide6.QtCore import QThread, Qt, QRect, QSize
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel
import pytest

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from tests.teams.matching.team_report_fixture import MatchingFixture
from tests.teams.matching.team_report_fixture import drain_dialog
from tests.teams.matching.team_report_fixture import wait_for
from src.domain.teams.models import Team
from src.domain.teams.report_matching import MatchStatus, ReportRange
from src.features.teams.report_matching.report_match_controller import ReportMatchController
from src.features.teams.report_matching.report_match_dialog import TeamReportMatchDialog
from src.services.allocation.axis.team_axis_selection import TeamAxisBinding, TeamAxisSelection
from tests.allocation.test_team_axis_enhancement import choices


def test_picker_filters_and_confirms_a_formal_scope_without_auto_accept():
    application()
    team = Team("team", "零伤害辅助也在队内", (1, 2, 3, 4), 1)
    entries = (replace(choices().entries[0], has_first_half=True, has_second_half=True),)
    service = MatchingFixture(entries, (team,))
    dialog = TeamReportMatchDialog(service=service, team=team)
    try:
        dialog.show()
        wait_for(lambda: len(service.calls) == 2 and not dialog.is_running())
        assert dialog.list.topLevelItemCount() == 2
        assert dialog.selection is None
        item = dialog.list.topLevelItem(1)
        dialog.list.setCurrentItem(item)
        assert "下半场" in item.text(0)
        QTest.keyClick(dialog.buttons.button(QDialogButtonBox.Ok), Qt.Key_Space)
        wait_for(lambda: dialog.result() == QDialog.Accepted and not dialog.is_running())
        assert dialog.selection.candidate.range.scope == "second"
        assert dialog.selection.status == MatchStatus.MATCHED
        assert len(service.calls) == 3
    finally:
        drain_dialog(dialog)
        dispose(dialog)


def test_manual_raw_range_uses_same_checker_and_keyboard_cancel_keeps_selection_empty():
    application()
    team = Team("team", "时段队", (1, 2, 3, 4), 1)
    service = MatchingFixture(choices().entries[:1], (team,))
    dialog = TeamReportMatchDialog(service=service, team=team)
    try:
        dialog.show()
        wait_for(lambda: service.calls and not dialog.is_running())
        assert dialog.manual_section.content.isHidden()
        QTest.keyClick(dialog.manual_section.toggle, Qt.Key_Space)
        assert dialog.manual_section.content.isVisible()
        dialog.manual_report.setCurrentIndex(1)
        dialog.manual_scope.setCurrentIndex(dialog.manual_scope.findData("range"))
        dialog.start.setValue(.5)
        dialog.end.setValue(1.000001)
        dialog.manual_check.click()
        wait_for(lambda: len(service.calls) == 2 and not dialog.is_running())
        assert service.calls[-1][1] == ReportRange(1, "range", 500_000, 1_000_001)
        QTest.keyClick(dialog.buttons.button(QDialogButtonBox.Cancel), Qt.Key_Space)
        assert dialog.selection is None and not dialog.isVisible()
    finally:
        drain_dialog(dialog)
        dispose(dialog)


def test_explicit_new_report_overrides_old_team_configuration_without_selecting_a_team():
    from types import SimpleNamespace
    from src.features.allocation.team_axis.team_axis_dialog import TeamAxisDialog
    application()
    team = Team("team", "原队伍", (1, 2, 3, 4), 1)
    old_battle = SimpleNamespace(battle_record_id=1, start_us=None, end_us=None, detail_scope=None)
    old = TeamAxisSelection((TeamAxisBinding(team, (), SimpleNamespace(battle=old_battle, anchor_mode="original")),))
    requested = SimpleNamespace(battle_record_id=2, start_us=100, end_us=200, detail_scope=None)
    service = MatchingFixture(choices().entries, (team,))
    dialog = TeamAxisDialog(service=None, matching_service=service, teams=(team,), choices=choices(),
                            initial=old, initial_request=requested)
    try:
        dialog.show()
        wait_for(lambda: service.calls and not dialog.is_running())
        assert service.calls[-1][1] == ReportRange(2, "range", 100, 200)
        assert not any(box.isChecked() for box in dialog.findChildren(QCheckBox))
        assert any("#2" in label.text() for label in dialog.findChildren(QLabel))
        assert dialog.selection is None
    finally:
        drain_dialog(dialog)
        dispose(dialog)


def test_team_page_explicit_match_overrides_older_enhancement_report():
    from types import SimpleNamespace
    from src.features.allocation.team_axis.team_axis_dialog import TeamAxisDialog
    application()
    team = Team("team", "从队伍页进入", (1, 2, 3, 4), 1)
    old = TeamAxisSelection((TeamAxisBinding(team, (), SimpleNamespace(anchor_mode="original",
        battle=SimpleNamespace(battle_record_id=1, detail_scope=None, start_us=None, end_us=None))),))
    service = MatchingFixture(choices().entries, (team,))
    candidate = service.candidate(ReportRange(2))
    match = service.match_page(team, (candidate,)).results[0]
    dialog = TeamAxisDialog(service=None, matching_service=service, teams=(team,), choices=choices(), initial=old,
                            initial_team_id=team.team_id, initial_match=match)
    try:
        assert any(box.isChecked() for box in dialog.findChildren(QCheckBox))
        assert any("#2" in label.text() for label in dialog.findChildren(QLabel))
        assert not any("#1" in label.text() for label in dialog.findChildren(QLabel))
    finally:
        drain_dialog(dialog)
        dispose(dialog)


def test_explicit_existing_scope_is_not_duplicated_and_manual_controls_restore_raw_time():
    application()
    team = Team("team", "去重队伍", (1, 2, 3, 4), 1)
    service = MatchingFixture(choices().entries[:1], (team,))
    dialog = TeamReportMatchDialog(service=service, team=team, initial_range=ReportRange(1))
    try:
        dialog.show()
        wait_for(lambda: service.calls and not dialog.is_running())
        assert len(service.calls) == 1 and dialog.list.topLevelItemCount() == 1
        assert dialog.manual_report.currentData() == 1 and dialog.manual_scope.currentData() == "all"
    finally:
        drain_dialog(dialog)
        dispose(dialog)


def test_paging_keeps_previous_matches_and_scene_filter_does_not_choose_one():
    from src.domain.teams.report_matching import ReportDiscovery, discover_candidates
    application()
    team = Team("team", "分页队伍", (1, 2, 3, 4), 1)
    entries = tuple(replace(entry, environment_name=f"场景{i}") for i, entry in enumerate(choices().entries))
    class Paged(MatchingFixture):
        def discover(self, *, offset=0, cancelled=lambda: False):
            return ReportDiscovery((team,), discover_candidates(entries[offset:offset+1]), 1 if offset == 0 else None)
    service = Paged(entries, (team,))
    dialog = TeamReportMatchDialog(service=service, team=team)
    try:
        dialog.show()
        wait_for(lambda: service.calls and not dialog.is_running())
        assert dialog.list.topLevelItemCount() == 1 and dialog.more_button.isEnabled()
        dialog.more_button.click()
        wait_for(lambda: len(service.calls) == 2 and not dialog.is_running())
        assert dialog.list.topLevelItemCount() == 2 and not dialog.more_button.isEnabled()
        dialog.scene_filter.setCurrentIndex(dialog.scene_filter.findData("场景1"))
        assert dialog.list.topLevelItemCount() == 1 and dialog.selection is None
    finally:
        drain_dialog(dialog)
        dispose(dialog)


def test_unknown_evidence_is_not_displayed_as_confirmed_zero_matches():
    from src.domain.teams.report_matching import MatchPage, TeamReportMatch
    application()
    team = Team("team", "未知队伍", (1, 2, 3, 4), 1)
    class Missing(MatchingFixture):
        def match_page(self, team, candidates, *, cancelled=lambda: False, publish=None, **kwargs):
            result = TeamReportMatch(team, candidates[0], MatchStatus.UNAVAILABLE, "缺少配套组件")
            if publish:
                publish(result)
            return MatchPage((result,), 1, 1)
    service = Missing(choices().entries[:1], (team,))
    dialog = TeamReportMatchDialog(service=service, team=team)
    try:
        dialog.show()
        wait_for(lambda: dialog._results and not dialog.is_running())
        assert "尚未确认匹配数量" in dialog.status.text()
        dialog.status_filter.setCurrentIndex(dialog.status_filter.findData("error"))
        assert dialog.list.topLevelItemCount() == 1
        dialog.list.setCurrentItem(dialog.list.topLevelItem(0))
        assert not dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
    finally:
        drain_dialog(dialog)
        dispose(dialog)


def test_close_cancels_owner_and_waits_for_physical_worker_exit():
    from concurrent.futures import CancelledError
    application()
    entered = Event()
    team = Team("team", "取消队伍", (1, 2, 3, 4), 1)
    class Waiting(MatchingFixture):
        def match_page(self, team, candidates, *, cancelled=lambda: False, **kwargs):
            entered.set()
            while not cancelled():
                Event().wait(.005)
            raise CancelledError
    service = Waiting(choices().entries[:1], (team,))
    dialog = TeamReportMatchDialog(service=service, team=team)
    try:
        dialog.show()
        wait_for(entered.is_set)
        dialog.reject()
        wait_for(lambda: not dialog.is_running() and not dialog.isVisible())
        assert dialog.selection is None
    finally:
        drain_dialog(dialog)
        dispose(dialog)


def test_controller_serializes_replacement_discards_old_progress_and_delivers_on_gui_thread():
    application()
    controller = ReportMatchController()
    entered = Event()
    values, threads = [], []
    controller.result.connect(lambda value: (values.append(value), threads.append(QThread.currentThread())))
    def first(cancelled, publish):
        entered.set()
        wait = Event()
        while not cancelled():
            wait.wait(.005)
        publish("过期进度")
        return "过期结果"
    progress = []
    controller.progress.connect(progress.append)
    try:
        controller.submit(first)
        wait_for(entered.is_set)
        controller.submit(lambda cancelled, publish: "新结果")
        wait_for(lambda: not controller.is_running())
        assert values == ["新结果"] and progress == []
        assert threads == [application().thread()]
    finally:
        controller.close()
        wait_for(lambda: not controller.is_running())
        controller.deleteLater()


@pytest.mark.parametrize("theme", ["black"])
@pytest.mark.parametrize("width,height", [(640, 600)])
def test_match_dialog_actions_reachable_on_small_screen(theme, width, height, monkeypatch):
    from src.app import window_geometry
    # Show 过滤器与显式限位必须看到同一合成屏幕，不能混入本机真实屏幕。
    monkeypatch.setattr(window_geometry, "_available_geometry", lambda _dialog: QRect(0, 0, width, height))
    from src.app.theme import apply_app_theme
    from src.app.window_geometry import fit_dialog_to_available_screen
    apply_app_theme(application(), theme)
    team = Team("team", "小窗口队伍", (1, 2, 3, 4), 1)
    service = MatchingFixture(choices().entries, (team,))
    dialog = TeamReportMatchDialog(service=service, team=team)
    try:
        fit_dialog_to_available_screen(dialog, QSize(940, 760), available_geometry=QRect(0, 0, width, height))
        dialog.show()
        wait_for(lambda: service.calls and not dialog.is_running())
        dialog.manual_section.toggle.setChecked(True)
        dialog.manual_check.setFocus()
        dialog.manual_scroll.ensureWidgetVisible(dialog.manual_check)
        application().processEvents()
        for button in (dialog.buttons.button(QDialogButtonBox.Cancel), dialog.manual_check, dialog.refresh_button):
            assert not button.visibleRegion().isEmpty()
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().topLeft()))
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
        # 验收窗口与标题栏在工作区内，而不是固化公共helper的装饰边距。
        assert QRect(0, 0, width, height).contains(dialog.frameGeometry())
    finally:
        drain_dialog(dialog)
        dispose(dialog)
