# 验证简报后台回填、来源显式保存、账号取消及三主题窄屏可操作性。
from time import monotonic, sleep

import pytest
from PySide6.QtCore import QRect, QSize
from PySide6.QtWidgets import QDialogButtonBox, QLabel, QTextEdit

from auto_sync_ui_fixture import application, dispose
from src.domain.teams.models import TeamRole
from src.domain.teams.overview import SourceMode
from src.features.teams.overview_controller import TeamOverviewController
from src.features.teams.overview_dialogs import TeamSourceDialog
from src.features.teams.page import TeamPage
from team_overview_fixture import overview_case


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
        assert graduation.text() == "毕业率：尚未读取历史"
        controller.refresh((team,))
        wait_for(lambda: not controller.is_running())
        assert graduation.text() == "毕业率：尚未读取历史"
        assert not any("平均装备毕业率" in label.text() for label in page.findChildren(QLabel))
        controller.configure_source(team.team_id)
        dialog = controller._dialog
        wait_for(lambda: dialog._expected is not None and not dialog.is_running())
        dialog.mode.setCurrentIndex(dialog.mode.findData(SourceMode.GAME))
        assert service.source_choices(team)[0].mode == SourceMode.CURRENT
        dialog.buttons.button(QDialogButtonBox.Save).click()
        wait_for(lambda: controller._dialog is None and not controller.is_running())
        assert service.source_choices(team)[0].mode == SourceMode.GAME
        assert "当前游戏配装" in page._overview_labels[team.team_id]["source"].text()
        controller.show_details(team.team_id)
        assert controller._details
        details = next(iter(controller._details))
        assert details.windowTitle().startswith("成员与配装来源")
        body = details.findChild(QTextEdit).toPlainText()
        assert "来源快照" in body and "毕业率" not in body
        assert graduation.text() == "毕业率：尚未读取历史"
        controller.reset_account()
        assert not controller._details and not controller._results
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
    dialog = TeamSourceDialog(service=service, team=team, names={})
    try:
        fit_dialog_to_available_screen(dialog, QSize(640, 480), available_geometry=QRect(0, 0, 640, 600))
        dialog.show()
        wait_for(lambda: dialog._expected is not None and not dialog.is_running())
        dialog.mode.setCurrentIndex(dialog.mode.findData(SourceMode.SLOTS))
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
    from src.ui.damage_optimization_panel import damage_optimization_panel
    application()
    value = dict(objective="frozen_axis_expected_v1", result_kind="baseline", status="baseline_only", reason="模型未就绪")
    for state in ("队伍增强预览，尚未保存", "本次队伍增强与方案已保存"):
        panel = damage_optimization_panel(value, object_name="teamPreview", save_state=state)
        try:
            assert state in [label.text() for label in panel.findChildren(QLabel)]
        finally:
            dispose(panel)
