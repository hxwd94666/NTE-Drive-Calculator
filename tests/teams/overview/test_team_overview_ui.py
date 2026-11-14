# 验证简报后台回填、来源显式保存、账号取消及三主题窄屏可操作性。
from time import monotonic, sleep

import pytest
from PySide6.QtCore import QRect, QSize
from PySide6.QtWidgets import QDialogButtonBox, QLabel

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from src.domain.teams.models import TeamRole
from src.domain.teams.overview import SourceMode
from src.features.teams.overview_controller import TeamOverviewController
from src.features.teams.loadouts.loadout_dialog import TeamLoadoutDialog
from src.features.teams.page import TeamPage
from tests.teams.overview.team_overview_fixture import overview_case


def wait_for(predicate):
    deadline = monotonic() + 10
    while not predicate():
        application().processEvents()
        assert monotonic() < deadline, "overview owner did not reach expected state"
        sleep(.005)
    application().processEvents()


def test_overview_renders_after_skeleton_and_source_save_requires_confirmation(tmp_path):
    application()
    service, team, _ = overview_case(tmp_path)
    page = TeamPage()
    controller = TeamOverviewController(service_provider=lambda: service, page=page)
    page.show_teams((team,), tuple(TeamRole(key, f"角色{key}") for key in team.character_ids))
    page.show()
    try:
        graduation = page._overview_labels[team.team_id]["axis_graduation"]
        assert graduation.text() == "毕业率：未读取"
        controller.refresh((team,))
        wait_for(lambda: not controller.is_running())
        assert graduation.text() == "毕业率：未读取"
        assert not any("平均装备毕业率" in label.text() for label in page.findChildren(QLabel))
        controller.configure_loadouts(team.team_id)
        dialog = controller._dialog
        wait_for(lambda: dialog._expected is not None and not dialog.is_running())
        assert "4/4" in dialog.ready_count.text()
        assert len(dialog.mode_buttons) == 2
        assert not dialog.buttons.button(QDialogButtonBox.Save).isEnabled()
        dialog.mode_buttons[SourceMode.GAME].click()
        wait_for(lambda: not dialog.is_running())
        assert service.source_choices(team)[0].mode == SourceMode.CALC
        dialog.buttons.button(QDialogButtonBox.Save).click()
        wait_for(lambda: controller._dialog is None and not controller.is_running())
        assert service.source_choices(team)[0].mode == SourceMode.GAME
        assert "游戏" in page._overview_labels[team.team_id]["source"].accessibleName()
        controller.configure_loadouts(team.team_id)
        dialog = controller._dialog
        wait_for(lambda: dialog._expected is not None and not dialog.is_running())
        assert dialog.windowTitle().startswith("队伍配装")
        assert "来源快照" in dialog.diagnostics.text()
        assert graduation.text() == "毕业率：未读取"
        controller.reset_account()
        wait_for(lambda: not controller.is_running())
        assert not controller._results
    finally:
        controller.deactivate()
        wait_for(lambda: not controller.is_running())
        dispose(page)
        controller.deleteLater()


@pytest.mark.parametrize("theme", ["dark", "black", "light"])
def test_source_controls_fit_small_screen_and_cancel_never_writes(tmp_path, theme):
    from src.app.theme import apply_app_theme
    from src.app.window_geometry import fit_dialog_to_available_screen
    application()
    apply_app_theme(application(), theme)
    service, team, _ = overview_case(tmp_path)
    before = service.source_choices(team)[0]
    dialog = TeamLoadoutDialog(service=service, team=team, roles={})
    try:
        fit_dialog_to_available_screen(dialog, QSize(640, 480), available_geometry=QRect(0, 0, 640, 600))
        dialog.show()
        wait_for(lambda: dialog._expected is not None and not dialog.is_running())
        dialog.mode_buttons[SourceMode.CALC].click()
        for combo in dialog.slots.values():
            assert combo.isEnabled()
        for button in (dialog.buttons.button(QDialogButtonBox.Save), dialog.buttons.button(QDialogButtonBox.Cancel)):
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        QTest.keyClick(dialog, Qt.Key_Escape)
        wait_for(lambda: not dialog.is_running())
        assert service.source_choices(team)[0] == before and dialog.selection is None
    finally:
        dialog.reject()
        wait_for(lambda: not dialog.is_running())
        dispose(dialog)


def test_preview_and_saved_status_are_explicit_plain_ui_state():
    from PySide6.QtWidgets import QLabel
    from src.ui.equipment.damage_optimization_panel import damage_optimization_panel
    application()
    value = dict(objective="frozen_axis_expected_v1", result_kind="baseline", status="baseline_only", reason="模型未就绪")
    for state in ("队伍增强预览，尚未保存", "本次队伍增强与方案已保存"):
        panel = damage_optimization_panel(value, object_name="teamPreview", save_state=state)
        try:
            assert state in [label.text() for label in panel.findChildren(QLabel)]
        finally:
            dispose(panel)


def test_unselected_member_is_preview_only_until_explicit_partial_source_save(tmp_path):
    application()
    service, team, _slots = overview_case(tmp_path)
    # 多槽成员仍允许明确留空；唯一已保存槽位现按产品要求默认使用。
    from src.storage.sqlite.user_data_dao import UserDataDao
    with UserDataDao(service.database_path, account_id="overview") as dao:
        dao.create_loadout_slot(team.character_ids[0], "备用", slot_key="alternate")
    before, _ = service.source_choices(team)
    dialog = TeamLoadoutDialog(service=service, team=team, roles={})
    try:
        dialog.show()
        wait_for(lambda: dialog._expected is not None and not dialog.is_running())
        dialog.slots[team.character_ids[0]].setCurrentIndex(0)
        wait_for(lambda: not dialog.is_running())
        assert "3/4" in dialog.ready_count.text()
        assert service.source_choices(team)[0] == before
        assert dialog.buttons.button(QDialogButtonBox.Save).isEnabled()
        dialog.buttons.button(QDialogButtonBox.Save).click()
        wait_for(lambda: not dialog.is_running())
        saved, _ = service.source_choices(team)
        assert saved.mode == SourceMode.CALC
        assert saved.bindings == tuple(pair for pair in before.bindings if pair[0] != team.character_ids[0])
        assert dialog.selection == saved
    finally:
        dialog.reject()
        wait_for(lambda: not dialog.is_running())
        dispose(dialog)


def test_same_named_slots_remain_distinguishable_without_retargeting():
    from src.features.teams.loadouts.loadout_row import TeamLoadoutRow
    from src.integrations.preferences.locale_files import bundled_text_catalog
    application()
    row = TeamLoadoutRow(1, TeamRole(1, "成员"), catalog=bundled_text_catalog())
    try:
        row.populate((dict(character_id=1, slot_id=11, slot_name="同名"),
                      dict(character_id=1, slot_id=12, slot_name="同名")), 12)
        assert row.choice.currentData() == 12
        assert row.choice.itemText(1) != row.choice.itemText(2)
    finally:
        dispose(row)


def test_late_saved_dialog_does_not_notify_same_team_after_session_expired(tmp_path):
    application()
    service, team, _slots = overview_case(tmp_path)
    page = TeamPage()
    page.show_teams((team,), ())
    controller = TeamOverviewController(service_provider=lambda: service, page=page)
    notices = []
    controller.source_changed.connect(notices.append)
    try:
        controller.refresh((team,))
        wait_for(lambda: not controller.is_running())
        controller.configure_loadouts(team.team_id)
        dialog = controller._dialog
        wait_for(lambda: not dialog.is_running())
        # 模拟旧请求已完成提交，但关闭回执送达前账号代次失效；相同 team_id 也不能刷新新会话。
        dialog.selection = service.source_choices(team)[0]
        service._current = lambda: False
        dialog.reject()
        wait_for(lambda: controller._dialog is None)
        assert notices == []
    finally:
        controller.deactivate()
        wait_for(lambda: not controller.is_running())
        dispose(page)
        controller.deleteLater()


def test_closing_loadout_details_retries_overview_without_saving_or_invalidating_graduation(tmp_path):
    application()
    service, team, _slots = overview_case(tmp_path)
    page = TeamPage()
    page.show_teams((team,), ())
    page.show()
    controller = TeamOverviewController(service_provider=lambda: service, page=page)
    changes = []
    controller.source_changed.connect(changes.append)
    before = service.source_choices(team)[0]
    try:
        controller.refresh((team,))
        wait_for(lambda: not controller.is_running())
        page.show_overview_error(team.team_id, '合成读取失败')
        controller.configure_loadouts(team.team_id)
        dialog = controller._dialog
        wait_for(lambda: not dialog.is_running())
        dialog.reject()
        wait_for(lambda: controller._dialog is None and not controller.is_running())
        assert service.source_choices(team)[0] == before and changes == []
        assert '计算' in page._overview_labels[team.team_id]['source'].accessibleName()
    finally:
        controller.deactivate()
        wait_for(lambda: not controller.is_running())
        dispose(page)
        controller.deleteLater()


def test_leaving_page_closes_loadouts_without_starting_retry_worker(tmp_path, monkeypatch):
    application()
    service, team, _slots = overview_case(tmp_path)
    page = TeamPage()
    page.show_teams((team,), ())
    page.show()
    controller = TeamOverviewController(service_provider=lambda: service, page=page)
    try:
        controller.refresh((team,))
        wait_for(lambda: not controller.is_running())
        controller.configure_loadouts(team.team_id)
        wait_for(lambda: not controller.is_running())
        calls, load = [], service.load
        def counted(*args, **kwargs):
            calls.append(args)
            return load(*args, **kwargs)
        monkeypatch.setattr(service, 'load', counted)
        # 导航先 deactivate 再隐藏页面，因此此时页面仍可见也须禁止新读取。
        controller.deactivate()
        wait_for(lambda: controller._dialog is None and not controller.is_running())
        assert not calls
    finally:
        controller.deactivate()
        wait_for(lambda: not controller.is_running())
        dispose(page)
        controller.deleteLater()
