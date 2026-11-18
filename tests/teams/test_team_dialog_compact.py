# 验证紧凑结果窗口的内容自适应、诊断可达、明确键盘动作及只读生命周期。
from dataclasses import replace
from threading import Event

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from tests.teams.matching.team_report_fixture import wait_for
from tests.teams.matching.team_report_fixture import drain_dialog
from tests.teams.overview.team_overview_fixture import overview_case
from tests.teams.graduation.test_team_axis_graduation_history_ui import case
from tests.teams.graduation.test_team_axis_graduation_history_ui import cleanup
from tests.teams.enhancement.test_team_enhancement_history import save_result
from tests.teams.test_team_ux_remaining import sample_catalog
from tests.teams.test_team_ux_remaining import dump
from src.app.theme import apply_app_theme, current_theme_name
from src.domain.teams.models import Team
from src.features.teams.enhancement_history_dialog import TeamEnhancementHistoryDialog
from src.features.teams.graduation.axis_graduation_controller import TeamAxisGraduationController
from src.features.teams.graduation.axis_graduation_dialog import TeamAxisGraduationDialog


def assert_footer_accessible(dialog, footer, buttons):
    for button in buttons:
        assert not button.visibleRegion().isEmpty()
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))


@pytest.mark.parametrize('theme', ["black"])
@pytest.mark.parametrize('width', [600, 1040])
def test_diagnostics_collapsed_but_reachable_with_fixed_right_footer(tmp_path, monkeypatch, theme, width):
    from src.app import window_geometry
    app = application()
    previous = current_theme_name()
    apply_app_theme(app, theme)
    monkeypatch.setattr(window_geometry, '_available_geometry', lambda _window: QRect(0, 0, width, 780))
    catalog = sample_catalog() if width == 600 else None
    service, team, slots = overview_case(tmp_path)
    save_result(service, team, slots, before=100, after=95)
    before = dump(service.database_path)
    history = TeamEnhancementHistoryDialog(service=service, team=team, catalog=catalog)
    try:
        history.open()
        wait_for(lambda: bool(history._rows) and not history.is_running())
        app.processEvents()
        initial = history.size()
        assert history.technical_section.content.isHidden()
        assert '-5.00%' in history.headline.text() and history.status.text()
        assert '静态来源' in history.technical.text() and '静态来源' not in history.sources.toPlainText()
        assert_footer_accessible(history, history.footer, [history.buttons.button(QDialogButtonBox.Close)])
        history.technical_section.toggle.setFocus()
        QTest.keyClick(history.technical_section.toggle, Qt.Key_Space)
        app.processEvents(); app.processEvents()
        assert history.technical_section.content.isVisible() and history.size() == initial
        assert history.scroll.verticalScrollBar().value() > 0
        assert history.scroll.horizontalScrollBar().maximum() == 0
        history.tabs.setCurrentWidget(history.sources)
        app.processEvents()
        assert history.sources.isReadOnly() and history.size() == initial
        assert dump(service.database_path) == before
    finally:
        drain_dialog(history)
        dispose(history)
        apply_app_theme(app, previous)

    apply_app_theme(app, theme)
    directory = tmp_path / 'graduation'
    directory.mkdir()
    service, _inputs, client, _values, team = case(directory)
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    dialog = TeamAxisGraduationDialog(owner=owner, team=team, catalog=catalog)
    try:
        dialog.open()
        wait_for(lambda: not owner.is_running())
        app.processEvents()
        initial, calls = dialog.size(), len(client.operations)
        assert dialog.diagnostics_section.content.isHidden() and dialog.advanced.content.isHidden()
        assert dialog.status.text() and dialog.scope.text() and dialog.damage.text()
        assert callable(dialog.metric)
        assert_footer_accessible(dialog, dialog.footer, [dialog.current, dialog.save, dialog.stop])
        dialog.diagnostics_section.toggle.setFocus()
        QTest.keyClick(dialog.diagnostics_section.toggle, Qt.Key_Space)
        app.processEvents(); app.processEvents()
        assert dialog.diagnostics_section.content.isVisible()
        first_lines = dialog.evidence.mapTo(dialog.scroll.viewport(),
            QPoint(4, min(dialog.evidence.height() - 1, 2 * dialog.evidence.fontMetrics().lineSpacing())))
        assert dialog.scroll.viewport().rect().contains(first_lines)
        assert dialog.evidence.isReadOnly() and dialog.size() == initial
        dialog.tabs.setCurrentWidget(dialog.history_tab)
        app.processEvents()
        assert dialog.size() == initial and len(client.operations) == calls
        assert dialog.scroll.horizontalScrollBar().maximum() == 0
        assert_footer_accessible(dialog, dialog.footer, [dialog.current, dialog.save, dialog.stop])
    finally:
        cleanup(owner, dialog)
        apply_app_theme(app, previous)


def test_history_user_resize_survives_first_async_empty_response():
    app = application()
    entered, release = Event(), Event()

    class DelayedHistory:
        def enhancement_history(self, _team, **_kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('synthetic history gate timed out')
            return (), None

    dialog = TeamEnhancementHistoryDialog(service=DelayedHistory(), team=Team('empty', '合成空记录', (), 1))
    try:
        dialog.open()
        wait_for(entered.is_set)
        app.processEvents()
        dialog.resize(590, 340)
        chosen = dialog.size()
        release.set()
        wait_for(lambda: not dialog.is_running())
        app.processEvents(); app.processEvents()
        assert dialog.size() == chosen and not dialog.tabs.isVisible()
        assert dialog.status.text() and not dialog.more.isEnabled()
        assert_footer_accessible(dialog, dialog.footer, [dialog.buttons.button(QDialogButtonBox.Close)])
    finally:
        release.set()
        drain_dialog(dialog)
        dispose(dialog)


def test_history_initial_size_uses_rows_but_paging_does_not_resize_or_reselect(tmp_path):
    app = application()
    service, team, slots = overview_case(tmp_path)
    save_result(service, team, slots)
    row = service.enhancement_history(team)[0][0]
    before = dump(service.database_path)
    sizes = []
    for count in [1, 8]:
        rows = tuple(replace(row, result_id=f'synthetic-{index}') for index in range(count))

        class History:
            def enhancement_history(self, _team, **_kwargs):
                return rows, None

        dialog = TeamEnhancementHistoryDialog(service=History(), team=team)
        try:
            dialog.open()
            wait_for(lambda: bool(dialog._rows) and not dialog.is_running())
            app.processEvents(); app.processEvents()
            sizes.append(dialog.height())
            initial = dialog.size()
            dialog.list.setCurrentRow(count - 1)
            dialog._loaded(((replace(row, result_id='additional-synthetic'),), None))
            app.processEvents(); app.processEvents()
            assert dialog.size() == initial and dialog.list.currentRow() == count - 1
            assert dialog.details.isReadOnly() and dump(service.database_path) == before
        finally:
            drain_dialog(dialog)
            dispose(dialog)
    assert sizes[1] > sizes[0]
