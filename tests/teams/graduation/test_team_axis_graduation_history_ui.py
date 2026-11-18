# 用本例Qt窗口和合成账号验证异步保存恢复、只读分页、取消及三主题窄屏可操作性。
from dataclasses import replace
from copy import deepcopy
import json
import os
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton

from tests.support.auto_sync_ui_fixture import application
from tests.teams.matching.team_report_fixture import wait_for
from src.app.theme import apply_app_theme
from src.domain.teams.models import TeamDraft, TeamRole
from src.domain.teams.report_matching import ReportRange
from src.features.teams.graduation.axis_graduation_controller import AxisGraduationSelection, TeamAxisGraduationController
from src.features.teams.graduation.axis_graduation_dialog import TeamAxisGraduationDialog
from src.features.teams.controller import TeamController
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.teams.graduation.test_team_axis_graduation_service import fixture
from tests.teams.graduation.test_team_axis_graduation_service import solve


NTE_TEST_TIER = "core"


def case(tmp_path, *, stored=True):
    application()
    service, inputs, client, values, _ = fixture()
    path = tmp_path / "user.sqlite3"
    with UserDataDao(path, account_id=inputs.current.account_id) as dao:
        team = dao.save_team(TeamDraft("合成轴伤队伍", inputs.current.team.character_ids))
    inputs.current = replace(inputs.current, team=team, source=replace(inputs.current.source, team_id=team.team_id))
    inputs.database_path, inputs.account_id = path, inputs.current.account_id
    values["target"] = replace(values["target"], team_id=team.team_id)
    if stored:
        preview = solve(service, inputs, values)
        service.save_completed_result(preview, expected_target_revision=0, expected_selection_revision=0)
    return service, inputs, client, values, team


def cleanup(owner, dialog=None, gate=None):
    if dialog is not None:
        dialog.reject()
        dialog.deleteLater()
    owner.close()
    if gate is not None:
        gate.release.set()
    wait_for(lambda: not owner.is_running())
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application().processEvents()


class Gated:
    def __init__(self, inner, operation):
        self.inner, self.operation = inner, operation
        self.entered, self.release = Event(), Event()

    def __getattr__(self, name):
        method = getattr(self.inner, name)
        if name != self.operation:
            return method
        def run(*args, **kwargs):
            self.entered.set()
            if not self.release.wait(5):
                raise RuntimeError("SYNTHETIC_GATE_TIMEOUT")
            return method(*args, **kwargs)
        return run


def test_open_history_never_analyzes_and_explicit_restore_keeps_fixed_reference(tmp_path):
    service, inputs, client, _values, team = case(tmp_path)
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    dialog = TeamAxisGraduationDialog(owner=owner, team=team)
    dialog.open()
    try:
        wait_for(lambda: not owner.is_running())
        assert len(client.operations) == 1 and owner.preview is None and not owner.can_update
        assert "历史数学结果" in dialog.headline.text() and "100%标尺" in dialog.damage.text()
        assert "70%" not in dialog.headline.text() and dialog.restore.isEnabled()
        chosen = owner.history.selected_evaluation_id
        owner.restore_saved(chosen)
        wait_for(lambda: not owner.is_running())
        assert owner.can_update and owner.preview is None and len(client.operations) == 1
        old_reference = service.selected_result(team)[1].reference
        QTest.mouseClick(dialog.current, Qt.LeftButton)
        wait_for(lambda: not owner.is_running())
        assert owner.preview.reference == old_reference and len(client.operations) == 2
        assert owner.preview.request.current.generation == inputs.current.generation
        QTest.mouseClick(dialog.save, Qt.LeftButton)
        wait_for(lambda: not owner.is_running())
        selected, saved = service.selected_result(team)
        assert selected.evaluation_id != chosen and saved.reference == old_reference
        assert owner.state == "saved_math" and len(client.operations) == 2
    finally:
        cleanup(owner, dialog)


def test_unsaved_preview_is_captured_for_explicit_save_and_stale_preview_stays_revoked(tmp_path):
    service, _inputs, client, values, team = case(tmp_path, stored=False)
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    owner.set_selection(AxisGraduationSelection(team, ReportRange(1), values["target"], values["rules"]))
    try:
        owner.load_history()
        wait_for(lambda: not owner.is_running())
        assert not owner.save_preview()
        owner.run_benchmark(**{k: values[k] for k in ("seeds", "max_evaluations", "max_millis")})
        wait_for(lambda: not owner.is_running())
        assert service.selected_result(team) == (None, None) and owner.can_save
        preview = owner.preview
        assert owner.save_preview()
        wait_for(lambda: not owner.is_running())
        assert owner.preview is preview and owner.history.selection_revision == 1
        owner.invalidate_current(team.team_id)
        owner.load_history()
        wait_for(lambda: not owner.is_running())
        assert owner.current_stale and not owner.can_save and not owner.save_preview()
        assert len(client.operations) == 1
    finally:
        cleanup(owner)


@pytest.mark.parametrize("operation", ["load_history", "save_completed_result"])
def test_account_cancel_discards_queued_database_intent_and_owner_drains_physically(tmp_path, operation):
    service, inputs, _client, values, team = case(tmp_path, stored=False)
    proxy = Gated(service, operation)
    owner = TeamAxisGraduationController(service_provider=lambda: proxy)
    owner.set_selection(AxisGraduationSelection(team, ReportRange(1), values["target"], values["rules"]))
    results = []
    owner.history_ready.connect(results.append)
    try:
        if operation == "save_completed_result":
            owner.load_history()
            wait_for(lambda: not owner.is_running())
            owner.run_benchmark(**{k: values[k] for k in ("seeds", "max_evaluations", "max_millis")})
            wait_for(lambda: not owner.is_running())
            results.clear()
            owner.save_preview()
        else:
            owner.load_history()
        wait_for(proxy.entered.is_set)
        inputs.active = False
        owner.reset_account()
        assert owner.is_running() and owner.preview is None and owner.history is None
        proxy.release.set()
        wait_for(lambda: not owner.is_running())
        assert results == [] and owner.team is None
        with UserDataDao(inputs.database_path) as dao:
            assert dao.get_team_axis_selection(team.team_id) is None
            assert dao.get_team_axis_target(team.team_id) is None
    finally:
        cleanup(owner, gate=proxy)


def test_offline_history_is_readable_without_analysis_and_restore_conflict_preserves_old_view(tmp_path):
    service, _inputs, client, _values, team = case(tmp_path)
    def forbidden(**_kwargs):
        raise AssertionError("history operation must not start analysis")
    service._factory = forbidden
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    owner.set_team(team)
    failures = []
    owner.error.connect(failures.append)
    try:
        owner.load_history()
        wait_for(lambda: not owner.is_running())
        view = owner.history
        assert view.selected_evaluation_id and len(client.operations) == 1
        service.select_saved_result(team, view.selected_evaluation_id, expected_revision=view.selection_revision)
        owner.restore_saved(view.selected_evaluation_id)
        wait_for(lambda: not owner.is_running())
        assert failures and owner.state == "failed" and owner.history == view and not owner.can_update
    finally:
        cleanup(owner)


def test_reentrant_account_reset_on_history_state_revokes_followup_display_signals(tmp_path):
    service, _inputs, _client, _values, team = case(tmp_path)
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    owner.set_team(team)
    histories = []
    owner.history_ready.connect(histories.append)
    owner.state_changed.connect(lambda state: owner.reset_account() if state == "history_ready" else None)
    try:
        owner.load_history()
        wait_for(lambda: not owner.is_running())
        assert histories == [] and owner.history is None and owner.team is None
    finally:
        cleanup(owner)


def test_native_advice_display_keeps_joint_gain_and_distinguishes_zero_from_unknown():
    from src.features.teams.graduation.axis_graduation_presentation import axis_graduation_display
    path = os.environ.get("NTE_TEAM_AXIS_GRADUATION_ADVICE_WIRE_RESULT")
    if not path:
        pytest.skip("explicit native synthetic advice response required")
    value = json.loads(Path(path).read_text("utf8"))
    assert value["fixture_kind"] == "synthetic_graduation_advice_wire_v1"
    request = json.loads(value["request_bytes"])["request"]
    response = value["response"]
    view = axis_graduation_display(response, request["target"], rules=request["rules"])
    assert "联合养成（独立整队重放）" in view.advice and "角色等级" in view.advice
    delta = response["recommendations"]["items"][0]["impact"]["damage_delta"]
    assert f"{delta:,.2f}" in view.advice
    assert "基础技能" in view.target and "副属性参考池" in view.target and "最低套装数" in view.target
    unknown = deepcopy(response)
    unknown["current"]["axis_evaluation"].update(damage=None)
    unknown_view = axis_graduation_display(unknown, request["target"])
    assert "当前轴伤（期望）：未知" in unknown_view.damage and "已建模部分" in unknown_view.damage
    unknown["current"]["axis_evaluation"].update(damage=0)
    assert "当前轴伤（期望）：0.00" in axis_graduation_display(unknown, request["target"]).damage


def test_evaluation_paging_does_not_change_selection_or_repeat_math(tmp_path):
    service, inputs, client, values, team = case(tmp_path)
    selected, initial = service.selected_result(team)
    from src.domain.teams.graduation.axis_graduation_snapshot import freeze_axis_context_snapshot
    snapshot = freeze_axis_context_snapshot(initial.to_payload()["response"]["axis_snapshot"])
    for n in range(11):
        task = service.prepare_current(team, ReportRange(1), target=values["target"], rules=values["rules"],
            reference=initial.reference, axis_snapshot=snapshot, request_id=f"synthetic-page-{n}")
        preview = service.evaluate_current(task)
        service.save_completed_result(preview, expected_target_revision=1, expected_selection_revision=1, select=False)
    count = len(client.operations)
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    dialog = TeamAxisGraduationDialog(owner=owner, team=team)
    dialog.open()
    try:
        wait_for(lambda: not owner.is_running())
        assert owner.history.more_evaluations and len(owner.history.evaluations) == 10
        dialog.tabs.setCurrentWidget(dialog.history_tab)
        application().processEvents()
        dialog.scroll.ensureWidgetVisible(dialog.enext)
        application().processEvents()
        assert not dialog.enext.visibleRegion().isEmpty()
        QTest.mouseClick(dialog.enext, Qt.LeftButton)
        wait_for(lambda: not owner.is_running())
        assert owner.history.evaluation_offset == 10 and len(owner.history.evaluations) == 2
        assert not owner.history.more_evaluations and dialog.eprev.isEnabled()
        assert len(client.operations) == count and service.selected_result(team)[0] == selected
        assert inputs.active
    finally:
        cleanup(owner, dialog)


def test_team_card_entry_and_account_reset_close_only_its_graduation_window(tmp_path):
    service, _inputs, _client, _values, team = case(tmp_path)
    controller = TeamController(service_provider=lambda: None, axis_graduation_service_provider=lambda: service)
    controller._teams = {team.team_id: team}
    controller.page.show_teams((team,), tuple(TeamRole(key, str(key)) for key in team.character_ids))
    controller.page.show()
    try:
        button = next(b for b in controller.page.findChildren(QPushButton) if b.text() == "毕业率")
        QTest.mouseClick(button, Qt.LeftButton)
        wait_for(lambda: not controller.is_running())
        dialog = controller._graduation_dialog
        assert dialog is not None and dialog.isVisible()
        assert any("历史数学结果" in x.text() for x in controller.page.findChildren(QLabel))
        controller.reset_account()
        assert not dialog.isVisible() and controller.axis_graduation.history is None
    finally:
        cleanup(controller.axis_graduation, controller._graduation_dialog)
        controller.page.close()
        controller.page.deleteLater()
        controller.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize("theme", ["black"])
def test_theme_small_screen_keeps_window_and_keyboard_actions_accessible(tmp_path, monkeypatch, theme):
    from src.app import window_geometry
    app = application()
    original = (app.styleSheet(), app.palette(), app.property("nte_theme_preference"), app.property("nte_effective_theme"))
    apply_app_theme(app, theme)
    area = QRect(50, 40, 560, 520)
    monkeypatch.setattr(window_geometry, "_available_geometry", lambda _dialog: area)
    service, _inputs, _client, _values, team = case(tmp_path)
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    dialog = TeamAxisGraduationDialog(owner=owner, team=team)
    dialog.open()
    try:
        wait_for(lambda: not owner.is_running())
        assert area.contains(dialog.frameGeometry())
        assert dialog.scroll.horizontalScrollBar().maximum() == 0
        dialog.tabs.setCurrentWidget(dialog.history_tab)
        app.processEvents()
        dialog.restore.setFocus()
        dialog.scroll.ensureWidgetVisible(dialog.restore)
        app.processEvents()
        assert dialog.restore.hasFocus() and not dialog.restore.visibleRegion().isEmpty()
        QTest.keyClick(dialog.restore, Qt.Key_Space)
        wait_for(lambda: not owner.is_running())
        assert owner.can_update
        QTest.keyClick(dialog, Qt.Key_Escape)
        assert not dialog.isVisible()
    finally:
        cleanup(owner, dialog)
        app.setStyleSheet(original[0])
        app.setPalette(original[1])
        app.setProperty("nte_theme_preference", original[2])
        app.setProperty("nte_effective_theme", original[3])
