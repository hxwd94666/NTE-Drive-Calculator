# 验证兼顾模式单窗分组、独立目标、旧模式隔离、来源失效与长译文底栏，不连接游戏。
from collections import Counter
from copy import deepcopy
from time import monotonic
from threading import Event

import pytest
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QLabel, QPushButton, QTableWidget
from shiboken6 import isValid

from tests.support.auto_sync_ui_fixture import dispose
from tests.support.qt_fonts import fonts as application
from src.domain.localization.catalog import make_catalog
from src.features.toolbox import page
from src.features.toolbox.rewind.dual_overlap_dialog import RewindDualOverlapDialog
from src.features.toolbox.rewind.dual_role_picker import DualRoleSelectionDialog
from src.integrations.preferences.locale_files import bundled_text_catalog
from src.ui.common.text_catalog import TextActionButton
from src.services.rewind.dual_recommendation import guard_payload
from src.services.rewind.recommendation import RewindLoadoutReader, RewindTargetRole
from tests.rewind.test_dual_target_service import scene
from tests.rewind.test_slot_selection import SHAPES
from tests.rewind.preferences_fixture import ProfileServiceFixture


class UiService:
    asset_root = None

    def __init__(self, inner):
        self.inner = inner
        self.saved = dict(strategy='balanced', target_grade='S', saved_rewind_shape_ids=['EquipmentGeometry_Hen2'] * 8)
        self.dual = dict(groups={
            'improve': dict(slots={'1004': 1}, grade='A', mode='grade', custom_percent=None),
            'push': dict(slots={'1005': 2}, grade='SSS', mode='grade', custom_percent=None)})
        self.requests = []

    def load_preferences(self):return deepcopy(self.saved)
    def load_dual_target_preferences(self):return deepcopy(self.dual)
    def save_preferences(self, value, **_kwargs):self.saved = deepcopy(value)

    def save_dual_target_preferences(self, value, *, guard=None, checkpoint=lambda: None):
        checkpoint()
        # 与正式保存一致：只拒绝静态换版，库存或配装更新照常覆盖。
        if guard and guard.static_identity != self.inner.static_identity():raise ValueError('rewind_source_changed')
        self.dual.update(deepcopy(value))
        if 'saved_rewind_shape_ids' in value:
            self.dual.update(saved_guard=guard_payload(guard) if guard else None,
                             plan_origin='dual_target' if guard else 'manual')

    def analyze_dual_target_shortfalls(self, request, **kwargs):
        self.requests.append(request)
        return self.inner.analyze_dual_target_shortfalls(request, **kwargs)

    def validate_dual_target_guard(self, *args, **kwargs):return self.inner.validate_dual_target_guard(*args, **kwargs)
    def validate_selection(self, *args):return self.inner.validate_selection(*args)
    def static_identity(self):return self.inner.static_identity()


def setup_dialog(tmp_path, monkeypatch, *, catalog=None, generation=None):
    application()
    monkeypatch.setattr(page.QTimer, 'singleShot', lambda *_: None)
    inner, dao, _request = scene(tmp_path)
    service = UiService(inner)
    dialog = page._RewindRecommendationDialog(ProfileServiceFixture(service), None, text_catalog=catalog, operation_generation=generation)
    reader = RewindLoadoutReader(dao, SHAPES)
    roles = tuple(RewindTargetRole(row['character_id'], str(row['character_id']), None,
        slots=(reader.inspect(row),)) for row in dao.slots)
    dialog._on_roles_loaded(roles)
    return dialog, service, dao, roles


def wait_until(predicate):
    deadline = monotonic() + 6
    while not predicate() and monotonic() < deadline:
        QApplication.processEvents();QTest.qWait(5)
    assert predicate()


def cleanup(dialog):
    worker = dialog._analysis_worker
    if worker is not None and isValid(worker):
        wait_until(lambda: not isValid(worker) or not worker.isRunning())
    QDialog.done(dialog, QDialog.Rejected)
    dispose(dialog)


def test_three_profiles_restore_independent_roles_targets_options_and_manual_drafts(tmp_path, monkeypatch):
    from dataclasses import replace
    dialog, service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    profile_service = dialog._service
    expected = {}
    try:
        for index, mode in enumerate(('balanced', 'focused', 'dual_target')):
            dialog._set_strategy(mode)
            dialog._target_character_ids = {1004} if index % 2 == 0 else {1005}
            dialog._main_character_ids = {1005} if index % 2 == 0 else {1004}
            dialog._set_target_grade(('A', 'SS', 'ACE')[index])
            dialog._rewind_options = replace(dialog._rewind_options, qualities=(('blue', 'purple', 'gold')[index],))
            dialog._begin_manual_draft()
            shape = dialog._replacement_candidates[index]
            dialog._editable_slots = [shape] * 8
            assert dialog._save_preferences()
            expected[mode] = (set(dialog._target_character_ids), set(dialog._main_character_ids),
                              dialog._target_grade, dialog._rewind_options.qualities, shape.shape.shape_id)
        for mode, values in expected.items():
            dialog._set_strategy(mode)
            assert (dialog._target_character_ids, dialog._main_character_ids, dialog._target_grade,
                    dialog._rewind_options.qualities, dialog._editable_slots[0].shape.shape_id) == values
        assert service.requests == []
    finally:
        cleanup(dialog)
    reopened = page._RewindRecommendationDialog(profile_service, None)
    try:
        values = expected['dual_target']
        assert reopened._strategy_key == 'dual_target'
        assert (reopened._target_character_ids, reopened._main_character_ids, reopened._target_grade,
                reopened._rewind_options.qualities, reopened._editable_slots[0].shape.shape_id) == values
        assert reopened._manual_plan_draft and reopened._dual_manual_draft
        assert not reopened._saved_rewind_shape_ids or reopened._saved_rewind_shape_ids == ('EquipmentGeometry_Hen2',) * 8
    finally:
        cleanup(reopened)


@pytest.mark.parametrize('theme', ["black"])
@pytest.mark.parametrize('target', ['focused', 'improve', 'push'])
def test_custom_percentage_double_click_replaces_whole_value_without_early_save(tmp_path, monkeypatch, theme, target):
    from src.app.theme import apply_app_theme, current_theme_name, install_dialog_defaults
    app = application()
    previous = current_theme_name()
    apply_app_theme(app, theme)
    install_dialog_defaults(app)
    dialog, service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        if target == 'focused':
            dialog._set_strategy('focused')
            dialog._set_custom_target()
            spin = dialog._custom_percent_input
        else:
            dialog._set_strategy('dual_target')
            grade = dialog._dual_grades[target]
            grade.setCurrentIndex(grade.findData('custom'))
            spin = dialog._dual_percent[target]
        spin.setValue(11.1)
        dialog.show();app.processEvents()
        editor = spin.lineEdit()
        for text, value in [('72.4', 72.4), ('3.2', 3.2), ('100.0', 100.)]:
            before = spin.value()
            editor.setFocus()
            QTest.mouseDClick(editor, Qt.LeftButton, pos=editor.rect().center())
            assert editor.selectedText().removesuffix('%') == f'{before:.1f}'
            QTest.keyClicks(editor, text)
            assert spin.value() == before  # 输入过程中不提交半个数值，也不改变已保存目标。
            QTest.keyClick(editor, Qt.Key_Tab)
            app.processEvents()
            assert spin.value() == value
            saved = (service.saved['target_custom_percent'] if target == 'focused'
                     else service.dual['groups'][target]['custom_percent'])
            assert saved == value
        assert service.requests == []
    finally:
        cleanup(dialog)
        apply_app_theme(app, previous)


def test_dual_targets_are_independent_and_edits_never_generate_implicitly(tmp_path, monkeypatch):
    dialog, service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        dialog._set_strategy('dual_target')
        assert not dialog._legacy_grade_panel.isVisibleTo(dialog)
        assert dialog._dual_panel.isVisibleTo(dialog)
        assert dialog._dual_request().improve.grade == 'A'
        assert dialog._dual_request().push.grade == 'SSS'
        grade = dialog._dual_grades['improve']
        grade.setCurrentIndex(grade.findData('custom'))
        dialog._dual_percent['improve'].setValue(35)
        assert dialog._dual_request().improve.custom_percent == 35
        assert dialog._dual_request().push.custom_percent is None
        assert service.saved['target_grade'] == 'S' and service.requests == []
        assert service.dual['groups']['improve']['custom_percent'] == 35
        dialog._set_strategy('balanced')
        assert dialog._saved_rewind_shape_ids == ('EquipmentGeometry_Hen2',) * 8
        assert dialog._target_grade == 'S'
        assert not dialog._recommendation_invalidated and dialog._save_plan_button.isEnabled()
    finally:cleanup(dialog)


def test_one_picker_keeps_groups_mutually_exclusive_and_cancellation_private(tmp_path, monkeypatch):
    dialog, _service, _dao, roles = setup_dialog(tmp_path, monkeypatch)
    selected = {key: dict(state['slots']) for key, state in dialog._dual_groups.items()}
    picker = DualRoleSelectionDialog(dialog, roles=roles, groups=selected,
                                     active_group='improve', catalog=dialog.text_catalog)
    try:
        cards = {identifier: card for card, identifier, _ in picker._cards}
        assert not cards[1005].isEnabled() and cards[1004].isChecked()
        picker._set_visible_checked(True)
        assert picker.selected_character_ids() == (1004,)
        picker.group_selector.setCurrentIndex(picker.group_selector.findData('push'))
        assert not cards[1004].isEnabled() and cards[1005].isChecked()
        result = picker.selected_groups()
        assert set(result['improve']) == {1004} and set(result['push']) == {1005}
        assert result['improve'][1004] == roles[0].slots[0].reference
        picker.reject()
        assert selected == {key: state['slots'] for key, state in dialog._dual_groups.items()}
    finally:
        dispose(picker);cleanup(dialog)


def test_generate_save_invalidate_and_stale_guard_leave_legacy_pool_untouched(tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(page.QMessageBox, 'warning', lambda *_args: errors.append(_args))
    dialog, service, dao, roles = setup_dialog(tmp_path, monkeypatch)
    try:
        old = deepcopy(service.saved)
        dialog._set_strategy('dual_target')
        dialog._refresh_analysis()
        wait_until(lambda: dialog._generated_analysis is not None or errors)
        assert not errors
        assert len(service.requests) == 1
        assert dialog._slots_complete()
        # 共同形状表在“共同缺口”弹窗内；结果区只提供已启用的入口。
        assert dialog._result_tabs.widget(0).findChild(QTableWidget) is None
        assert dialog._result_tabs.widget(0).findChild(QPushButton, 'rewindDualOverlap').isEnabled()
        dialog._save_plan()
        assert service.saved['saved_rewind_shape_ids'] == old['saved_rewind_shape_ids']
        assert service.dual['saved_rewind_shape_ids'] == ['EquipmentGeometry_Hen4'] * 8
        assert service.dual['saved_guard']['slots'][0]['source_snapshot_id'] == 3
        # 生成后库存已更新：再次保存照常覆盖，原冻结来源不变，也不隐式降为手动方案。
        original_analysis = dialog._generated_analysis
        dialog._generated_analysis = None
        dao.current_inventory_snapshot_id = lambda: 99
        saved_guard = deepcopy(service.dual['saved_guard'])
        dialog._save_plan()
        assert not errors and service.dual['saved_guard'] == saved_guard
        assert service.dual['plan_origin'] == 'dual_target'
        monkeypatch.setattr(dialog, '_confirm_rewind_source_change', lambda _changes: False)
        assert not dialog._check_saved_plan()
        dao.current_inventory_snapshot_id = lambda: 9
        old_token, old_result = dialog._analysis_token, dialog._generated_analysis
        dialog._dual_grades['push'].setCurrentIndex(dialog._dual_grades['push'].findData('ACE'))
        assert dialog._recommendation_invalidated and not dialog._save_plan_button.isEnabled()
        assert not dialog._check_saved_plan()
        dialog._on_analysis_ready(old_token, original_analysis)
        assert dialog._generated_analysis is old_result
        reopened = page._RewindRecommendationDialog(ProfileServiceFixture(service), None)
        try:
            assert reopened._recommendation_invalidated and not reopened._save_plan_button.isEnabled()
            reopened._on_roles_loaded(roles)
            assert not reopened._check_saved_plan()
        finally:cleanup(reopened)
    finally:cleanup(dialog)


def test_save_after_generation_is_not_blocked_by_later_inventory_or_loadout_updates(tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(page.QMessageBox, 'warning', lambda *_args: errors.append(_args))
    dialog, service, dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        dialog._set_strategy('dual_target')
        result = service.inner.analyze_dual_target_shortfalls(dialog._dual_request())
        token = object();dialog._analysis_token = token
        dialog._on_analysis_ready(token, result)
        assert dialog._save_plan_button.isEnabled()
        dao.current_inventory_snapshot_id = lambda: 99
        dao.slots[0]['current_plan']['plan_id'] += 1
        dialog._save_plan()
        # 保存直接覆盖八槽并原样记录生成时来源，不读取新库存或新方案重绑。
        assert not errors
        assert service.dual['saved_rewind_shape_ids'] == [slot.shape.shape_id for slot in dialog._editable_slots]
        assert service.dual['saved_guard'] == guard_payload(result.dual_guard)
    finally:cleanup(dialog)


def test_empty_intersection_keeps_both_group_explanations_and_empty_slots(tmp_path, monkeypatch):
    dialog, service, dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        dialog._set_strategy('dual_target')
        for key in dao.slots[0]['current_plan']['payload']['assignment_scores']:
            dao.slots[0]['current_plan']['payload']['assignment_scores'][key] = 100
        result = service.inner.analyze_dual_target_shortfalls(dialog._dual_request())
        dialog._generated_analysis = result
        dialog._render_plans(result)
        assert not any(dialog._editable_slots)
        assert not dialog._save_plan_button.isEnabled()
        assert any('暂无共同缺口' in label.text() for label in dialog._result_tabs.widget(0).findChildren(QLabel))
    finally:cleanup(dialog)


def test_manual_adjustment_keeps_source_guard_and_recomputes_display_cost(tmp_path, monkeypatch):
    dialog, service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        dialog._set_strategy('dual_target')
        result = service.inner.analyze_dual_target_shortfalls(dialog._dual_request())
        token = object()
        dialog._analysis_token = token
        dialog._on_analysis_ready(token, result)
        replacement = next(row for row in dialog._replacement_candidates if row.shape.shape_id == 'EquipmentGeometry_Hen2')
        dialog._editable_slots[0] = replacement
        dialog._render_dual_results(result, preserve_edits=True)
        assert dialog._editable_slots[0] is replacement
        quantities = Counter(slot.shape.shape_id for slot in dialog._editable_slots if slot is not None)
        overlap = RewindDualOverlapDialog(dialog, analysis=result, quantities=quantities,
                                          edited=dialog._dual_results_edited, text_catalog=dialog.text_catalog)
        metrics = [label.text() for label in overlap.findChildren(QLabel, 'rewindDualMetricValue')]
        assert metrics[-1] == '290 萝卜币' and '手动调整' in overlap.findChild(QLabel, 'rewindDualEdited').text()
        assert result.recommendations[0].quantity == 8 and result.dual_guard is not None
        dialog._save_plan()
        assert service.dual['saved_guard'] == guard_payload(result.dual_guard)
        dialog._set_strategy('balanced');dialog._set_strategy('dual_target')
        assert not dialog._recommendation_invalidated and dialog._save_plan_button.isEnabled()
        dialog._save_plan()
        assert service.dual['saved_guard'] == guard_payload(result.dual_guard)
    finally:cleanup(dialog)


def test_old_generation_does_not_save_or_accept_a_late_result(tmp_path, monkeypatch):
    generation = [1]
    dialog, service, _dao, _roles = setup_dialog(tmp_path, monkeypatch, generation=lambda: generation[0])
    try:
        dialog._set_strategy('dual_target')
        result = service.inner.analyze_dual_target_shortfalls(dialog._dual_request())
        token = object();dialog._analysis_token = token
        before = deepcopy(service.dual)
        generation[0] = 2
        assert dialog._save_dual_draft() is False
        dialog._on_analysis_ready(token, result)
        assert dialog._generated_analysis is None and service.dual == before
    finally:cleanup(dialog)


def test_input_change_cancels_inflight_work_and_preserves_old_saved_pool(tmp_path, monkeypatch):
    dialog, service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    entered, release = Event(), Event()
    errors = []
    monkeypatch.setattr(page.QMessageBox, 'warning', lambda *_args: errors.append(_args))
    try:
        dialog._set_strategy('dual_target')
        result = service.inner.analyze_dual_target_shortfalls(dialog._dual_request())
        token = object();dialog._analysis_token = token;dialog._on_analysis_ready(token, result)
        dialog._save_plan()
        old_slots = tuple(dialog._editable_slots)
        saved_pool = deepcopy(service.dual['saved_rewind_shape_ids'])
        saved_guard = deepcopy(service.dual['saved_guard'])
        def delayed(request, *, checkpoint, **kwargs):
            entered.set()
            assert release.wait(4)
            return service.inner.analyze_dual_target_shortfalls(request, checkpoint=checkpoint, **kwargs)
        monkeypatch.setattr(service, 'analyze_dual_target_shortfalls', delayed)
        dialog._refresh_analysis();wait_until(entered.is_set)
        dialog._dual_grades['push'].setCurrentIndex(dialog._dual_grades['push'].findData('ACE'))
        release.set()
        worker = dialog._analysis_worker
        wait_until(lambda: not isValid(worker) or not worker.isRunning())
        QApplication.processEvents()
        assert dialog._generated_analysis is result and tuple(dialog._editable_slots) == old_slots
        assert dialog._recommendation_invalidated and not dialog._save_plan_button.isEnabled()
        assert service.dual['saved_rewind_shape_ids'] == saved_pool and service.dual['saved_guard'] == saved_guard
        assert not errors
    finally:
        release.set();cleanup(dialog)


@pytest.mark.parametrize('theme', ["black"])
def test_long_patch_keeps_dual_controls_and_footer_reachable(tmp_path, monkeypatch, theme):
    from src.app.theme import apply_app_theme, current_theme_name
    application();base = bundled_text_catalog();old_theme = current_theme_name()
    messages = {key: '［Long translated label］ ' + value for key, value in base.messages.items() if key.startswith('rewind.dual.')}
    catalog = make_catalog(dict(format_version=1, catalog_id='nte-ui', locale='en-US', display_name='Fixture', messages=messages), base=base)
    dialog, service, _dao, roles = setup_dialog(tmp_path, monkeypatch, catalog=catalog)
    picker = None
    try:
        apply_app_theme(QApplication.instance(), theme)
        dialog._set_strategy('dual_target')
        dialog.resize(960, 740);dialog.show();QApplication.processEvents()
        result = service.inner.analyze_dual_target_shortfalls(dialog._dual_request())
        dialog._render_plans(result);QApplication.processEvents()
        for button in (dialog._generate_button, dialog._save_plan_button, dialog._start_rewind_button):
            point = button.mapTo(dialog, button.rect().center())
            assert dialog.rect().contains(point) and button.isVisibleTo(dialog)
        for grade in dialog._dual_grades.values():
            assert grade.isVisibleTo(dialog) and grade.width() > 30
        assert dialog._result_tabs.widget(0).findChild(QPushButton, 'rewindDualOverlap').isVisibleTo(dialog)
        cards = dialog.findChildren(QFrame, 'rewindShapeRecommendationCard')
        assert len(cards) == 8 and all(card.isVisibleTo(dialog) for card in cards)
        scroll = dialog._results_scroll
        scroll.ensureWidgetVisible(cards[-1]);QApplication.processEvents()
        assert scroll.viewport().rect().contains(cards[-1].mapTo(scroll.viewport(), cards[-1].rect().center()))
        picker = DualRoleSelectionDialog(dialog, roles=roles,
            groups={key: state['slots'] for key, state in dialog._dual_groups.items()},
            active_group='push', catalog=catalog)
        picker.resize(900, 620);picker.show();QApplication.processEvents()
        confirm = picker.findChild(TextActionButton, 'rewindRoleConfirm')
        for control in [picker.group_selector, confirm]:
            assert control.isVisibleTo(picker) and picker.rect().contains(control.mapTo(picker, control.rect().center()))
    finally:
        if picker is not None:
            QDialog.done(picker, QDialog.Rejected);dispose(picker)
        cleanup(dialog);apply_app_theme(QApplication.instance(), old_theme)
