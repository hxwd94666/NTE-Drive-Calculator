# 验证结构化毕业目标、只读预设切换、单层滚动与紧凑历史行，不改变分析或持久化语义。
from copy import deepcopy
import json

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QScrollArea, QTextEdit
from PySide6.QtGui import QWheelEvent

from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from tests.teams.matching.team_report_fixture import wait_for
from tests.teams.graduation.test_team_axis_graduation_history_ui import case
from tests.teams.graduation.test_team_axis_graduation_history_ui import cleanup
from tests.teams.test_team_ux_remaining import dump
from tests.teams.test_team_ux_remaining import sample_catalog
from tests.teams.test_team_dialog_compact import assert_footer_accessible
from src.app.theme import apply_app_theme, current_theme_name
from src.domain.localization.catalog import make_catalog
from src.domain.teams.models import TeamRole
from src.features.teams.graduation.axis_graduation_presentation import axis_graduation_display
from src.features.teams.graduation.axis_graduation_controller import TeamAxisGraduationController
from src.features.teams.graduation.axis_graduation_dialog import TeamAxisGraduationDialog
from src.features.teams.graduation.target_view import GraduationTargetView
from src.integrations.preferences.locale_files import bundled_text_catalog


def pump():
    for _ in range(5):
        application().processEvents()


@pytest.mark.parametrize('theme', ["black"])
@pytest.mark.parametrize('width', [580, 1100])
def test_target_and_advice_have_only_outer_scroll_and_right_footer(tmp_path, monkeypatch, theme, width):
    from src.app import window_geometry
    app = application()
    previous = current_theme_name()
    apply_app_theme(app, theme)
    monkeypatch.setattr(window_geometry, '_available_geometry', lambda _dialog: QRect(0, 0, width, 800))
    service, inputs, client, _values, team = case(tmp_path)
    before = dump(inputs.database_path)
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    roles = {key: TeamRole(key, f'合成角色 {key}') for key in team.character_ids}
    catalog = sample_catalog() if width == 580 else None
    dialog = TeamAxisGraduationDialog(owner=owner, team=team, roles=roles, catalog=catalog)
    try:
        dialog.open()
        wait_for(lambda: not owner.is_running())
        pump()
        initial, calls = dialog.size(), len(client.operations)
        payload = json.loads(owner.history.display.target_json)
        dialog.tabs.setCurrentIndex(1)
        pump()
        assert not dialog.target.findChildren(QScrollArea)
        assert not dialog.target.findChildren(QTextEdit) and not dialog.target.findChildren(QPlainTextEdit)
        assert not isinstance(dialog.advice, (QScrollArea, QTextEdit, QPlainTextEdit))
        assert dialog.target.details_section.content.isHidden()
        assert dialog.status.isVisible() and dialog.scope.isVisible()
        assert dialog.damage.isHidden() and dialog.headline.isHidden()
        assert dialog.target.version.text() and dialog.target.summary.text()
        assert dialog.target.details.text() == owner.history.display.target
        assert dialog.target.preset.count() == len(payload['presets'])
        labels = [label for label in dialog.target.members.findChildren(QLabel) if label.isVisible()]
        assert sum(label.pixmap() is not None and not label.pixmap().isNull() for label in labels) == 4
        assert all(any(role.name == label.text() for label in labels) for role in roles.values())
        if dialog.target.preset.count() > 1:
            dialog.target.preset.setCurrentIndex(1)
            pump()
            assert dialog.target.preset.currentData() == payload['presets'][1]['preset_id']
        dialog.target.details_section.toggle.click()
        pump()
        dialog.scroll.verticalScrollBar().setValue(dialog.scroll.verticalScrollBar().maximum())
        pump()
        end = dialog.target.details.mapTo(dialog.scroll.viewport(), QPoint(4, dialog.target.details.height() - 4))
        assert dialog.scroll.viewport().rect().contains(end)
        assert_footer_accessible(dialog, dialog.footer, [dialog.current, dialog.save, dialog.stop])
        text = '<b>纯文本建议</b>\n' + '\n'.join(f'建议 {i}：仅用于检查完整滚动，不执行计算。' for i in range(60)) + '\n建议末尾'
        dialog.tabs.setCurrentIndex(2)
        dialog.advice.setText(text)
        pump()
        dialog.scroll.verticalScrollBar().setValue(0)
        point = QPoint(8, 8)
        wheel = QWheelEvent(QPointF(point), QPointF(dialog.advice.mapToGlobal(point)), QPoint(),
                            QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        app.sendEvent(dialog.advice, wheel)
        assert not wheel.isAccepted()  # 文本不吞滚轮；sendEvent 本身不经过平台的父级冒泡。
        propagated = QWheelEvent(QPointF(dialog.advice.mapTo(dialog.scroll.viewport(), point)),
            QPointF(dialog.advice.mapToGlobal(point)), QPoint(), QPoint(0, -120),
            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        app.sendEvent(dialog.scroll.viewport(), propagated)
        pump()
        assert propagated.isAccepted() and dialog.scroll.verticalScrollBar().value() > 0
        dialog.scroll.verticalScrollBar().setValue(dialog.scroll.verticalScrollBar().maximum())
        pump()
        assert dialog.advice.text() == text and dialog.advice.textFormat() == Qt.PlainText
        end = dialog.advice.mapTo(dialog.scroll.viewport(), QPoint(4, dialog.advice.height() - 4))
        assert dialog.scroll.viewport().rect().contains(end)
        assert dialog.scroll.horizontalScrollBar().maximum() == 0
        assert dialog.size() == initial and len(client.operations) == calls
        assert dump(inputs.database_path) == before
        dialog.tabs.setCurrentIndex(0)
        pump()
        assert dialog.damage.isVisible() and dialog.headline.isVisible()
        assert_footer_accessible(dialog, dialog.footer, [dialog.current, dialog.save, dialog.stop])
    finally:
        cleanup(owner, dialog)
        apply_app_theme(app, previous)


def test_preset_view_is_an_independent_display_and_retains_complete_parameters():
    from tests.teams.graduation.test_team_axis_graduation_requests import prepared
    application()
    _frozen, values = prepared()
    payload = values['target'].to_payload()
    payload['presets'] = [payload['presets'][0], deepcopy(payload['presets'][0])]
    payload['presets'][1]['preset_id'] = 'other-display-preset'
    payload['presets'][1]['members'][0]['character_level'] = 73
    original = deepcopy(payload)
    view = GraduationTargetView()
    try:
        view.set_target(payload, '完整参数末尾')
        view.show()
        pump()
        view.preset.setCurrentIndex(1)
        pump()
        visible = '\n'.join(label.text() for label in view.members.findChildren(QLabel) if label.isVisible())
        assert 'Lv73' in visible and view.details.text() == '完整参数末尾'
        assert view.details_section.content.isHidden() and payload == original
        payload['presets'][1]['members'][0]['character_level'] = 1
        view.preset.setCurrentIndex(0)
        view.preset.setCurrentIndex(1)
        pump()
        assert any('Lv73' in label.text() for label in view.members.findChildren(QLabel) if label.isVisible())
        assert view.preset.currentData() == 'other-display-preset'
    finally:
        dispose(view)


def test_legacy_text_stays_available_without_invented_target_summary():
    application()
    view = GraduationTargetView()
    try:
        view.set_target(None, '旧记录完整参数，不从译文猜字段')
        assert view.version.text() == bundled_text_catalog().render('teams.target_view.legacy')
        assert view.details.text() == '旧记录完整参数，不从译文猜字段'
        assert view.summary.isHidden() and view.members.isHidden()
    finally:
        dispose(view)


def test_display_carries_frozen_target_and_draft_keeps_structured_summary(tmp_path):
    service, inputs, client, values, team = case(tmp_path)
    selected, saved = service.selected_result(team)
    body = saved.to_payload()
    target = saved.target.to_payload()
    view = axis_graduation_display(body['response'], target, rules=body['request']['rules'], historical=True)
    expected = deepcopy(target)
    target['world_bonus']['yaodao_attack_add'] = 0
    assert json.loads(view.target_json) == expected
    assert view.target == axis_graduation_display(body['response'], expected, rules=body['request']['rules'], historical=True).target
    before = dump(inputs.database_path)
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    dialog = TeamAxisGraduationDialog(owner=owner, team=team)
    try:
        dialog.open()
        wait_for(lambda: not owner.is_running())
        calls = len(client.operations)
        # 模拟明确的目标草稿应用；不保存或运行求解。
        from src.domain.teams.report_matching import ReportRange
        dialog._axis, dialog._draft_target, dialog._draft_rules = ReportRange(1), values['target'], values['rules']
        dialog._apply_draft()
        assert dialog.target.preset.count() == len(values['target'].presets)
        assert dialog.target.version.text() == bundled_text_catalog().render('teams.target_view.version', revision=values['target'].revision)
        assert dump(inputs.database_path) == before and len(client.operations) == calls
        assert selected is not None
    finally:
        cleanup(owner, dialog)


def test_history_selectors_are_adjacent_and_do_not_compute(tmp_path):
    service, _inputs, client, _values, team = case(tmp_path)
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    dialog = TeamAxisGraduationDialog(owner=owner, team=team)
    try:
        dialog.open()
        wait_for(lambda: not owner.is_running())
        calls = len(client.operations)
        dialog.tabs.setCurrentWidget(dialog.history_tab)
        pump()
        first = dialog.benchmarks.mapTo(dialog.history_tab, dialog.benchmarks.rect().bottomLeft())
        second = dialog.evaluations.mapTo(dialog.history_tab, dialog.evaluations.rect().topLeft())
        assert second.y() - first.y() <= max(dialog.benchmarks.fontMetrics().lineSpacing(), dialog.evaluations.fontMetrics().lineSpacing())
        dialog.scroll.ensureWidgetVisible(dialog.restore)
        pump()
        assert not dialog.restore.visibleRegion().isEmpty() and len(client.operations) == calls
    finally:
        cleanup(owner, dialog)


def test_new_target_keys_support_old_patch_fallback_and_matching_parameters():
    base = bundled_text_catalog()
    sample = sample_catalog()
    old = make_catalog(dict(format_version=1, catalog_id='nte-ui', locale='en-US', display_name='Old', messages={}), base=base)
    keys = [key for key in base.messages if key.startswith(('teams.target_view.', 'teams.graduation.history_'))]
    assert keys
    for key in keys:
        assert old.messages[key] == base.messages[key]
        assert sample.parameters[key] == base.parameters[key]
