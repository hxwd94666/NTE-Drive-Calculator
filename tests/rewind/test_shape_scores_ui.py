# 验证手选驱动弹窗的缺分/多分、超出八种形状时可手选空槽，以及兼顾模式“共同缺口”弹窗，不连接游戏。
import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QTableWidget, QToolButton

from src.domain.rewind.shape_overview import DriveMargin, summarize_shape_margins

from src.app import window_geometry
from src.domain.rewind.rewind_loadout import RewindSlotReference
from src.features.toolbox.rewind import dual_overlap_dialog, execution_dialog
from tests.rewind.test_dual_target_ui import cleanup, setup_dialog


def capture(monkeypatch, cls):
    shown = []

    def fake_exec(instance):
        shown.append(instance)
        return 0
    monkeypatch.setattr(cls, 'exec', fake_exec)
    return shown


def generate(dialog, analysis):
    token = object()
    dialog._analysis_token = token
    dialog._on_analysis_ready(token, analysis)


def legacy_analysis(service, **kwargs):
    return service.inner.analyze_for_targets(target_character_ids=(1004,),
                                             selected_slots=(RewindSlotReference(1004, 1, 11),), **kwargs)


def picker_scores(picker):
    return {label.property('shapeId'): label.text()
            for label in picker.findChildren(QLabel, 'rewindShapeReplacementScore')}


HINT = '点击“生成推荐”可生成库存/缺分/多分数据，黄框是已选择驱动。'


def in_slots(picker):
    return [button.property('shapeId') for button in picker.findChildren(QToolButton, 'rewindShapeReplacementOption')
            if button.property('inSlots')]


def test_picker_shows_only_stock_until_a_recommendation_is_generated(tmp_path, monkeypatch):
    shown = capture(monkeypatch, execution_dialog.RewindShapeReplacementDialog)
    dialog, service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        dialog._edit_rewind_slot(0)
        assert not picker_scores(shown[-1])
        assert shown[-1].findChild(QLabel, 'rewindShapeScoresHint').text() == HINT
        # 黄框表示当前八槽已选择的形状，与是否有分值无关；载入的已保存方案是 8 份 Hen2。
        assert in_slots(shown[-1]) == ['EquipmentGeometry_Hen2']
        generate(dialog, legacy_analysis(service))
        slots = list(dialog._editable_slots)
        dialog._edit_rewind_slot(0)
        scores = picker_scores(shown[-1])
        assert len(scores) == 12
        # 5 件 Hen4 各保存 10 分、S 目标 20 分：缺分合计 50；其余形状未被所选方案使用。
        assert scores['EquipmentGeometry_Hen4'] == '缺 50 分'
        assert scores['EquipmentGeometry_Hen2'] == '未使用'
        assert in_slots(shown[-1]) == ['EquipmentGeometry_Hen4']
        assert shown[-1].findChild(QLabel, 'rewindShapeScoresHint').text() == HINT
        assert dialog._editable_slots == slots
        dialog._set_target_grade('ACE')
        dialog._edit_rewind_slot(0)
        assert not picker_scores(shown[-1])
    finally:cleanup(dialog)


def test_shape_limit_keeps_eight_empty_slots_for_scored_manual_picking(tmp_path, monkeypatch):
    shown = capture(monkeypatch, execution_dialog.RewindShapeReplacementDialog)
    dialog, service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        analysis = legacy_analysis(service, selection_limit=0)
        assert analysis.notice and not analysis.recommendations
        generate(dialog, analysis)
        page = dialog._result_tabs.widget(0)
        assert len(page.findChildren(QFrame, 'rewindShapeRecommendationCard')) == 8
        assert not any(dialog._editable_slots) and not dialog._save_plan_button.isEnabled()
        assert any(analysis.notice in label.text() for label in page.findChildren(QLabel))
        dialog._edit_rewind_slot(0)
        assert picker_scores(shown[-1])['EquipmentGeometry_Hen4'] == '缺 50 分'
    finally:cleanup(dialog)


def test_dual_picker_sums_both_groups_against_their_own_targets(tmp_path, monkeypatch):
    shown = capture(monkeypatch, execution_dialog.RewindShapeReplacementDialog)
    dialog, service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        dialog._set_strategy('dual_target')
        generate(dialog, service.inner.analyze_dual_target_shortfalls(dialog._dual_request()))
        dialog._edit_rewind_slot(0)
        # 低分组 A 目标 16、高分组 SSS 目标 28，两份方案各 5 件 10 分：缺 30 + 90。
        assert picker_scores(shown[-1])['EquipmentGeometry_Hen4'] == '缺 120 分'
    finally:cleanup(dialog)


def test_overlap_button_only_in_dual_mode_and_needs_current_result(tmp_path, monkeypatch):
    dialog, service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        generate(dialog, legacy_analysis(service))
        assert dialog._result_tabs.widget(0).findChild(QPushButton, 'rewindDualOverlap') is None
        dialog._set_strategy('dual_target')
        button = dialog._result_tabs.widget(0).findChild(QPushButton, 'rewindDualOverlap')
        assert button is not None and not button.isEnabled() and button.toolTip()
        generate(dialog, service.inner.analyze_dual_target_shortfalls(dialog._dual_request()))
        button = dialog._result_tabs.widget(0).findChild(QPushButton, 'rewindDualOverlap')
        assert button.isEnabled()
        dialog._dual_grades['push'].setCurrentIndex(dialog._dual_grades['push'].findData('ACE'))
        assert not button.isEnabled()
    finally:cleanup(dialog)


def test_overlap_dialog_lists_shared_shapes_with_both_groups(tmp_path, monkeypatch):
    shown = capture(monkeypatch, dual_overlap_dialog.RewindDualOverlapDialog)
    dialog, service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        dialog._set_strategy('dual_target')
        generate(dialog, service.inner.analyze_dual_target_shortfalls(dialog._dual_request()))
        tab = dialog._result_tabs.widget(0)
        assert tab.findChild(QTableWidget) is None and tab.findChild(QLabel, 'rewindDualStatus') is None
        tab.findChild(QPushButton, 'rewindDualOverlap').click()
        overlap, = shown
        cards = overlap.findChildren(QFrame, 'rewindDualOverlapCard')
        assert [(card.property('shapeId'), card.property('demand')) for card in cards] == [
            ('EquipmentGeometry_Hen4', 'shared')]
        groups = [label.text() for label in cards[0].findChildren(QLabel, 'rewindDualOverlapGroup')]
        assert groups == ['低分组 缺 30 分', '高分组 缺 90 分']
        assert cards[0].findChild(QLabel, 'rewindDualOverlapSlots').text() == '八槽 8 份'
        icon = cards[0].findChild(QLabel, 'rewindDualOverlapIcon')
        assert max(icon.pixmap().width(), icon.pixmap().height()) >= 80
        metrics = [label.text() for label in overlap.findChildren(QLabel, 'rewindDualMetricValue')]
        assert metrics == ['1 种', '5 / 5 件', '5 / 5 件', '360 萝卜币']
        assert overlap.findChild(QTableWidget) is None
    finally:cleanup(dialog)


def test_saved_dual_plan_cards_do_not_claim_zero_shortfall(tmp_path, monkeypatch):
    dialog, _service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        dialog._set_strategy('dual_target')
        dialog._editable_slots = [dialog._replacement_candidates[0]] * 8
        dialog._render_rewind_slots('t', 'd')
        labels = dialog.findChildren(QLabel, 'rewindShapeMetrics')
        assert labels and not any('缺件' in label.text() for label in labels)
    finally:cleanup(dialog)


@pytest.mark.parametrize('screen', [QRect(0, 0, 1920, 1080), QRect(0, 0, 1024, 600)])
def test_overlap_dialog_fits_screen_without_large_blank_area(tmp_path, monkeypatch, screen):
    monkeypatch.setattr(window_geometry, '_available_geometry', lambda _: screen)
    shown = capture(monkeypatch, dual_overlap_dialog.RewindDualOverlapDialog)
    dialog, service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        dialog._set_strategy('dual_target')
        generate(dialog, service.inner.analyze_dual_target_shortfalls(dialog._dual_request()))
        dialog._result_tabs.widget(0).findChild(QPushButton, 'rewindDualOverlap').click()
        overlap, = shown
        assert overlap.width() <= screen.width() - 48 and overlap.height() <= screen.height() - 48
        # 只有一张卡片时窗口按内容收紧，不撑出整屏空白。
        assert overlap.height() < 520
    finally:cleanup(dialog)


def test_picker_shows_shortfall_when_any_drive_is_below_target_otherwise_surplus():
    rows = summarize_shape_margins([DriveMargin(1, 1, 'A', 2, 25, 20), DriveMargin(2, 2, 'A', 2, 18, 20),
                                    DriveMargin(1, 1, 'B', 2, 20, 20), DriveMargin(1, 1, 'D', 2, 25, 20)])
    totals = {row.shape_id: row.totals for row in rows}
    text = lambda key, **values: {'rewind.picker.short': '缺 {score} 分', 'rewind.picker.over': '多 {score} 分',
                                  'rewind.picker.even': '持平', 'rewind.picker.unused': '未使用'}[key].format(**values)
    # A 有一件缺 2 分：与八槽卡片“缺分”同口径，只累计低于目标的部分，不被另一件的富余抵消。
    assert [execution_dialog._score_text(text, totals.get(key)) for key in ('A', 'B', 'D', 'C')] == [
        '缺 2 分', '持平', '多 5 分', '未使用']


def test_one_drive_below_target_shows_shortfall_even_when_others_exceed_it(tmp_path, monkeypatch):
    shown = capture(monkeypatch, execution_dialog.RewindShapeReplacementDialog)
    dialog, service, dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        # 全面均衡常见情形：同形状一件低于目标、其余件高于目标；仍按缺分显示，与八槽卡片和推荐算法一致。
        scores = dao.slots[0]['current_plan']['payload']['assignment_scores']
        for index, key in enumerate(sorted(scores)):
            scores[key] = 10 if index == 0 else 30
        generate(dialog, legacy_analysis(service))
        dialog._edit_rewind_slot(0)
        label = next(label for label in shown[-1].findChildren(QLabel, 'rewindShapeReplacementScore')
                     if label.property('shapeId') == 'EquipmentGeometry_Hen4')
        assert label.text() == '缺 10 分'
    finally:cleanup(dialog)


def synthetic_overlap(count):
    from types import SimpleNamespace as NS
    from src.domain.rewind.rewind_shape_recommendation import RewindPricingRule
    from src.domain.equipment.geometry.shapes import OFFICIAL_SHAPE_LABELS
    shapes = list(OFFICIAL_SHAPE_LABELS)[:count]
    margins = tuple(DriveMargin(index + 1, index + 1, shape, 2, 5, 10, group)
                    for index, shape in enumerate(shapes) for group in ('improve', 'push'))
    result = NS(shared=[NS(shape_id=shape) for shape in shapes], uncovered_shapes=(),
                groups=(NS(items=[1] * count, examined_count=count), NS(items=[1] * count, examined_count=count)))
    return NS(dual_target=result, drive_margins=margins, pricing_rule=RewindPricingRule())


@pytest.mark.parametrize('screen', [QRect(0, 0, 1920, 1080), QRect(0, 0, 1280, 800)])
def test_overlap_cards_fit_five_per_row_and_window_height_is_capped(monkeypatch, screen):
    from tests.support.qt_fonts import fonts
    fonts()
    monkeypatch.setattr(window_geometry, '_available_geometry', lambda _: screen)
    overlap = dual_overlap_dialog.RewindDualOverlapDialog(None, analysis=synthetic_overlap(12), quantities={}, edited=False)
    try:
        cards = overlap.findChildren(QFrame, 'rewindDualOverlapCard')
        assert len(cards) == 12
        rows = {}
        for card in cards:
            rows.setdefault(card.mapTo(overlap, card.rect().topLeft()).y(), []).append(card)
        assert max(len(row) for row in rows.values()) == 5
        # 卡片多时只滚动卡片区，窗口高度不超过屏幕可用高度的约六成。
        assert overlap.height() <= screen.height() * 0.62
    finally:
        overlap.deleteLater()


def pick(monkeypatch, shape_id):
    def fake_exec(instance):
        instance._set_selected(shape_id)
        return execution_dialog.QDialog.DialogCode.Accepted
    monkeypatch.setattr(execution_dialog.RewindShapeReplacementDialog, 'exec', fake_exec)


def card_metrics(dialog):
    return [label.text() for label in dialog._result_tabs.widget(0).findChildren(QLabel, 'rewindShapeMetrics')]


def test_hand_picked_shape_card_uses_the_same_frozen_shortfall_as_the_picker(tmp_path, monkeypatch):
    dialog, service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        generate(dialog, legacy_analysis(service, selection_limit=0))
        pick(monkeypatch, 'EquipmentGeometry_Hen4')
        dialog._edit_rewind_slot(0)
        assert dialog._editable_slots[0].quality_gap == 50
        assert any('缺分 50.00' in text for text in card_metrics(dialog))
    finally:cleanup(dialog)


def test_regenerating_clears_stale_shortfall_of_previously_recommended_shapes(tmp_path, monkeypatch):
    dialog, service, dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        generate(dialog, legacy_analysis(service))
        assert dialog._editable_slots[0].quality_gap == 50
        scores = dao.slots[0]['current_plan']['payload']['assignment_scores']
        for key in scores:
            scores[key] = 30
        analysis = legacy_analysis(service)
        assert analysis.notice and not analysis.recommendations
        generate(dialog, analysis)
        candidate = next(row for row in dialog._replacement_candidates if row.shape.shape_id == 'EquipmentGeometry_Hen4')
        assert candidate.quality_gap == 0 and candidate.suit_demand == 0
    finally:cleanup(dialog)
