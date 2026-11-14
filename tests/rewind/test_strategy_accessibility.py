# 验证倒带策略切换后窗口和必要内容保持可达。

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QLabel

from tests.rewind.test_dual_target_ui import cleanup, setup_dialog
from tests.support.theme import themed_app as themed_app
from tests.support.theme import settle
from src.app import window_geometry

def test_rewind_full_pool_and_dual_notice_stay_reachable_without_resizing(tmp_path, monkeypatch, themed_app):
    app = themed_app
    monkeypatch.setattr(window_geometry, '_available_geometry', lambda _: QRect(0, 0, 1920, 1080))
    dialog, _service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        dialog.show();settle(app)
        initial = dialog.size()
        for mode in ('balanced', 'focused', 'dual_target', 'balanced'):
            dialog._set_strategy(mode);settle(app)
            assert dialog.size() == initial
            assert dialog._results_scroll.verticalScrollBar().maximum() == 0
            assert dialog._dual_notice.isVisibleTo(dialog) == (mode == 'dual_target')
            assert dialog.rect().contains(dialog._save_plan_button.mapTo(dialog, dialog._save_plan_button.rect().center()))
        dialog._set_strategy('dual_target');settle(app)
        message = dialog._dual_notice.findChild(QLabel)
        assert message.text() == dialog.text_catalog.render('rewind.dual.explanation')
    finally:
        cleanup(dialog)

