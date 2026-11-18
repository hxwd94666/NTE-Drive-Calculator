# 验证倒带窗口切换、构造时窗口隔离和模式帮助，不操作游戏。
from tests.rewind.preferences_fixture import ProfileServiceFixture
from pathlib import Path
import json

import pytest
from PySide6.QtCore import QEvent, QObject, QRect, Qt
from PySide6.QtWidgets import QPushButton, QWidget

from tests.rewind.test_dual_target_ui import setup_dialog, cleanup
from tests.support.theme import themed_app as themed_app, settle
from src.domain.localization.catalog import make_catalog
from src.integrations.preferences.locale_files import bundled_text_catalog

@pytest.mark.parametrize('strategy', ['balanced', 'focused'])
def test_leaving_dual_target_keeps_window_stable_and_preserves_saved_pool(tmp_path, monkeypatch, strategy, themed_app):
    from src.app import window_geometry
    app = themed_app
    monkeypatch.setattr(window_geometry, '_available_geometry', lambda _dialog: QRect(0, 0, 1920, 1440))
    dialog, service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    try:
        saved = tuple(dialog._saved_rewind_shape_ids)
        dialog.show();settle(app)
        size = dialog.size()
        for _ in range(2):
            dialog._set_strategy('dual_target');settle(app)
            assert dialog.size() == size
            analysis = service.inner.analyze_dual_target_shortfalls(dialog._dual_request())
            dialog._render_dual_results(analysis);settle(app)
            assert dialog.size() == size
            assert dialog._result_tabs.widget(0).findChild(QPushButton, 'rewindDualOverlap') is not None
            assert dialog.size() == size
            # 用户可手动调整窗口；回切模式不应撤销这个尺寸，也不应强制回到曾记录的旧高度。
            dialog.resize(dialog.width(), 840);settle(app)
            size = dialog.size()
            dialog._set_strategy(strategy);settle(app)
            assert dialog.size() == size
        assert dialog._saved_rewind_shape_ids == saved
        assert service.saved['saved_rewind_shape_ids'] == list(saved)
        dual_button = dialog._strategy_buttons['dual_target']
        assert dual_button.toolTip() == ''
        dual_button.setText(dialog.text_catalog.render('rewind.dual.mode'))
        assert dual_button.toolTip() == ''
        for button in (dialog._save_plan_button, dialog._start_rewind_button):
            assert button.isVisibleTo(dialog) and dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
    finally:cleanup(dialog)


@pytest.mark.parametrize('initial_strategy', ['balanced', 'dual_target'])
def test_rewind_constructor_never_shows_unparented_custom_target_spinbox(tmp_path, monkeypatch, themed_app, initial_strategy):
    from tests.rewind.test_dual_target_ui import UiService, scene
    from src.features.toolbox import page

    class WindowShows(QObject):
        def __init__(self):
            super().__init__()
            self.events = []

        def eventFilter(self, watched, event):
            if event.type() == QEvent.Show and isinstance(watched, QWidget) and watched.isWindow():
                self.events.append((type(watched).__name__, watched.windowTitle()))
            return False

    app = themed_app
    inner, _dao, _request = scene(tmp_path)
    service = UiService(inner)
    service.saved['strategy'] = initial_strategy
    service.dual['groups']['improve'].update(mode='custom', custom_percent=35)
    service.dual['groups']['push'].update(mode='custom', custom_percent=65)
    monkeypatch.setattr(page.QTimer, 'singleShot', lambda *_: None)
    observer = WindowShows()
    app.installEventFilter(observer)
    dialog = None
    try:
        dialog = page._RewindRecommendationDialog(ProfileServiceFixture(service), None)
        settle(app)
        assert observer.events == [], observer.events
        dialog.show();settle(app)
        assert observer.events == [(type(dialog).__name__, dialog.windowTitle())]
    finally:
        app.removeEventFilter(observer)
        if dialog is not None:
            cleanup(dialog)


@pytest.mark.parametrize('strategy', ['balanced', 'dual_target'])
def test_strategy_help_covers_all_modes_in_both_entry_states(tmp_path, monkeypatch, strategy):
    from src.features.toolbox import page
    dialog, _service, _dao, _roles = setup_dialog(tmp_path, monkeypatch)
    received = []
    monkeypatch.setattr(page.QMessageBox, 'exec', lambda message: received.append((message.text(), message.textFormat())))
    try:
        dialog._set_strategy(strategy);dialog._show_strategy_help()
        body, text_format = received[0]
        assert text_format == Qt.PlainText
        paragraphs = body.split('\n\n')
        assert len(paragraphs) == 3
        assert all(part.startswith(name + '：') for part, name in
                   zip(paragraphs, ['全面均衡', '少角冲分', '兼顾模式']))
        assert dialog._strategy_buttons['dual_target'].toolTip() == ''
        base = bundled_text_catalog()
        old = make_catalog(dict(format_version=1, catalog_id='nte-ui', locale='en-US', display_name='Old', messages={}), base=base)
        sample = make_catalog(json.loads((Path(__file__).parents[2]/'docs/examples/locales/qps-ploc.json').read_text(encoding='utf8')), base=base)
        for key in ('rewind.strategy.help_hint', 'rewind.strategy.help_all_modes', 'allocation.priority.manage_accessible'):
            assert old.messages[key] == base.messages[key] and sample.parameters[key] == base.parameters[key]
    finally:cleanup(dialog)

