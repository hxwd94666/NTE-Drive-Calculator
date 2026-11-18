# 验证倒带分组操作、结果详情和小屏幕底栏可达，不操作游戏。
import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QFrame, QPushButton, QScrollArea

from tests.rewind.test_dual_target_ui import cleanup, setup_dialog
from tests.support.theme import settle
from tests.support.theme import themed_app as themed_app
from src.app import window_geometry

def test_dual_group_controls_precede_strategy_and_use_shared_role_actions(tmp_path, monkeypatch, themed_app):
    app = themed_app
    monkeypatch.setattr(window_geometry, '_available_geometry', lambda _: QRect(0, 0, 1920, 1080))
    dialog, service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        saved = tuple(dialog._saved_rewind_shape_ids)
        dialog.show();settle(app)
        size = dialog.size()
        for _ in range(2):
            dialog._set_strategy('dual_target');settle(app)
            strategy = dialog._strategy_buttons['dual_target']
            assert strategy.mapTo(dialog, QPoint()).y() > dialog._dual_panel.mapTo(dialog, dialog._dual_panel.rect().bottomLeft()).y()
            assert dialog.text_catalog.render('rewind.dual.improve') == '低分组'
            assert dialog.text_catalog.render('rewind.dual.push') == '高分组'
            for button in dialog._dual_buttons.values():
                assert button.text() == dialog._main_button.text() == '选择角色'
            assert dialog._results_scroll.verticalScrollBar().maximum() == 0
            assert dialog.size() == size
            dialog._set_strategy('focused');settle(app)
            assert dialog.size() == size and dialog._saved_rewind_shape_ids == saved
        assert service.requests == []
    finally:
        cleanup(dialog)


@pytest.mark.parametrize('strategy', ['balanced', 'focused', 'dual_target'])
def test_default_rewind_window_keeps_both_slot_rows_and_footer_visible(tmp_path, monkeypatch, themed_app, strategy):
    app = themed_app
    monkeypatch.setattr(window_geometry, '_available_geometry', lambda _: QRect(0, 0, 1920, 1440))
    dialog, _service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        dialog._set_strategy(strategy);dialog.show();settle(app)
        viewport = dialog._results_scroll.viewport()
        cards = dialog.findChildren(QFrame, 'rewindShapeRecommendationCard')
        assert len(cards) == 8
        assert dialog._results_scroll.verticalScrollBar().maximum() == 0
        for card in cards:
            rect = card.rect().translated(card.mapTo(viewport, QPoint()))
            assert viewport.rect().contains(rect)
        for button in (dialog._save_plan_button, dialog._start_rewind_button):
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
    finally:
        cleanup(dialog)


def test_dual_results_have_one_scroll_owner_and_overview_entry_remains_reachable(tmp_path, monkeypatch, themed_app):
    app = themed_app
    monkeypatch.setattr(window_geometry, '_available_geometry', lambda _: QRect(0, 0, 1920, 1080))
    dialog, service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        dialog._set_strategy('dual_target');dialog.show();settle(app)
        size = dialog.size()
        analysis = service.inner.analyze_dual_target_shortfalls(dialog._dual_request())
        dialog._render_plans(analysis);settle(app)
        assert dialog._results_scroll.findChildren(QScrollArea) == []
        cards = dialog.findChildren(QFrame, 'rewindShapeRecommendationCard')
        assert len(cards) == 8
        # 共同缺口说明与来源明细已移入“共同缺口”弹窗，结果区只保留其入口。
        assert dialog._result_tabs.widget(0).findChild(QPushButton, 'rewindDualOverlap').isVisibleTo(dialog)
        dialog._results_scroll.ensureWidgetVisible(cards[-1]);settle(app)
        viewport = dialog._results_scroll.viewport()
        assert viewport.rect().contains(cards[-1].mapTo(viewport, cards[-1].rect().center()))
        assert dialog.size() == size
        for button in (dialog._save_plan_button, dialog._start_rewind_button):
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
        assert service.requests == []
    finally:
        cleanup(dialog)


def test_smaller_screen_keeps_rewind_footer_reachable(tmp_path, monkeypatch, themed_app):
    app = themed_app
    monkeypatch.setattr(window_geometry, '_available_geometry', lambda _: QRect(0, 0, 1280, 800))
    dialog, _service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        dialog._set_strategy('dual_target');dialog.show();settle(app)
        assert dialog.height() <= 752
        for button in (dialog._generate_button, dialog._save_plan_button, dialog._start_rewind_button):
            assert button.isVisibleTo(dialog)
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
    finally:
        cleanup(dialog)

