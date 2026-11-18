# 验证毕业率三按钮的可见顺序、语言补丁、退出以及原更新/保存/停止绑定。
import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton

from tests.support.auto_sync_ui_fixture import application
from tests.teams.graduation.test_team_axis_graduation_history_ui import case
from tests.teams.graduation.test_team_axis_graduation_history_ui import cleanup
from tests.teams.graduation.test_team_axis_graduation_history_ui import wait_for
from tests.teams.graduation.test_team_axis_graduation_history_ui import Gated
from tests.teams.test_team_ux_remaining import sample_catalog
from tests.teams.test_team_ux_remaining import dump
from src.app.theme import apply_app_theme, current_theme_name
from src.integrations.preferences.locale_files import bundled_text_catalog
from src.features.teams.graduation.axis_graduation_controller import TeamAxisGraduationController
from src.features.teams.graduation.axis_graduation_dialog import TeamAxisGraduationDialog


@pytest.mark.parametrize('theme', ["black"])
@pytest.mark.parametrize('patched', [False, True])
def test_three_footer_actions_follow_visible_order_and_escape_still_exits(tmp_path, monkeypatch, theme, patched):
    from src.app import window_geometry
    app = application()
    previous = current_theme_name()
    apply_app_theme(app, theme)
    monkeypatch.setattr(window_geometry, '_available_geometry', lambda _dialog: QRect(0, 0, 600, 800))
    catalog = sample_catalog() if patched else bundled_text_catalog()
    service, inputs, client, _values, team = case(tmp_path)
    before = dump(inputs.database_path)
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    dialog = TeamAxisGraduationDialog(owner=owner, team=team, catalog=catalog)
    try:
        dialog.open()
        wait_for(lambda: not owner.is_running())
        app.processEvents()
        actions = sorted(dialog.footer.findChildren(QAbstractButton),
                         key=lambda button: button.mapTo(dialog.footer, button.rect().topLeft()).x())
        assert actions == [dialog.stop, dialog.current, dialog.save]
        for button, key in zip(actions, ['stop', 'current', 'save']):
            assert button.accessibleName() == catalog.render('teams.graduation.' + key)
            assert not button.visibleRegion().isEmpty()
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
        assert not dialog.stop.isEnabled() and not dialog.save.isEnabled()
        if not patched:
            assert [button.text() for button in actions] == ['停止计算', '更新毕业率', '保存结果']
        calls = len(client.operations)
        QTest.keyClick(dialog, Qt.Key_Escape)
        wait_for(lambda: not owner.is_running())
        assert not dialog.isVisible() and dump(inputs.database_path) == before
        assert len(client.operations) == calls
    finally:
        cleanup(owner, dialog)
        apply_app_theme(app, previous)


def test_update_and_save_keep_original_frozen_preview_flow(tmp_path):
    application()
    service, _inputs, client, _values, team = case(tmp_path)
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    dialog = TeamAxisGraduationDialog(owner=owner, team=team)
    try:
        dialog.open()
        wait_for(lambda: not owner.is_running())
        owner.restore_saved(owner.history.selected_evaluation_id)
        wait_for(lambda: not owner.is_running())
        calls = len(client.operations)
        assert dialog.current.isEnabled()
        dialog.current.click()
        wait_for(lambda: not owner.is_running())
        assert len(client.operations) == calls + 1 and owner.can_save and dialog.save.isEnabled()
        frozen = owner.preview
        dialog.save.click()
        wait_for(lambda: not owner.is_running())
        assert owner.state == 'saved_math' and service.selected_result(team)[1].reference == frozen.reference
        dialog.close()
        assert not dialog.isVisible()
    finally:
        cleanup(owner, dialog)


def test_stop_cancels_running_update_without_saving_or_closing(tmp_path):
    application()
    service, inputs, _client, _values, team = case(tmp_path)
    gate = Gated(service, 'evaluate_current')
    owner = TeamAxisGraduationController(service_provider=lambda: gate)
    dialog = TeamAxisGraduationDialog(owner=owner, team=team)
    try:
        dialog.open()
        wait_for(lambda: not owner.is_running())
        owner.restore_saved(owner.history.selected_evaluation_id)
        wait_for(lambda: not owner.is_running())
        before = dump(inputs.database_path)
        dialog.current.click()
        wait_for(gate.entered.is_set)
        assert dialog.stop.isEnabled() and not dialog.current.isEnabled() and not dialog.save.isEnabled()
        dialog.stop.click()
        gate.release.set()
        wait_for(lambda: not owner.is_running())
        assert dialog.isVisible() and not owner.can_save and dump(inputs.database_path) == before
    finally:
        cleanup(owner, dialog, gate)
