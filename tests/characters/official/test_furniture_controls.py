# 验证家具三项界面回填静默、草稿保护和完整数值读取，不使用全局关窗。
import pytest
from PySide6.QtWidgets import QApplication, QWidget

from src.domain.official_role.models import WorldBonusSettings
from src.features.official_role.furniture_controls import (
    apply_world_bonus_display, build_world_bonus_card, read_world_bonus_controls, world_bonus_controls,
)
from tests.support.qt_cleanup import dispose_windows_created_by_test as dispose_windows_created_by_test


NTE_TEST_TIER = 'core'


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    owner._official_role_world_bonus_dirty = False
    owner._my_role_dirty = False
    owner._official_role_editors = {}
    card = build_world_bonus_card(owner)
    card.setParent(owner)
    yield owner
    owner.close()
    owner.deleteLater()
    app.processEvents()


def test_three_inputs_silently_display_native_values(window):
    apply_world_bonus_display(window, WorldBonusSettings(6., .016, 15.))
    assert read_world_bonus_controls(window) == WorldBonusSettings(6., .016, 15.)
    assert not window._official_role_world_bonus_dirty and not window._my_role_dirty
    assert len(world_bonus_controls(window)) == 3
    assert all(control.accessibleName() for control in world_bonus_controls(window))


def test_edit_marks_manual_draft_and_background_update_does_not_overwrite(window):
    window.official_role_world_defense.setEnabled(True)
    window.official_role_world_defense.setValue(12)
    assert window._official_role_world_bonus_dirty and window._my_role_dirty
    apply_world_bonus_display(window, WorldBonusSettings(0., 0., 0.))
    assert read_world_bonus_controls(window) == WorldBonusSettings(20., .04, 12.)


def test_other_role_draft_survives_world_refresh(window):
    window._my_role_dirty = True
    window._official_role_editors = {1: {'detail': {'profile': {'character_level': 42},
        'world_bonus': WorldBonusSettings().to_payload(), '_role_damage_values': object()}}}
    apply_world_bonus_display(window, WorldBonusSettings(8., .02, 6.))
    detail = window._official_role_editors[1]['detail']
    assert detail['profile']['character_level'] == 42
    assert detail['world_bonus'] == WorldBonusSettings(8., .02, 6.).to_payload()
    assert '_role_damage_values' not in detail
    assert read_world_bonus_controls(window) == WorldBonusSettings(8., .02, 6.)


def test_retyping_default_value_is_an_explicit_manual_draft(window):
    from PySide6.QtTest import QTest
    window.official_role_world_attack.setEnabled(True)
    line = window.official_role_world_attack.lineEdit()
    line.selectAll()
    QTest.keyClicks(line, '20')
    window.official_role_world_attack.interpretText()
    assert window._official_role_world_bonus_dirty
    assert read_world_bonus_controls(window) == WorldBonusSettings()


def test_returning_to_loaded_draft_consumes_pending_furniture_recalculation(window):
    from src.features.official_role.role_shell import _populate_role_tab
    calls = []
    window._official_role_editors = {1: {'furniture_recalc_pending': True,
        'request_calculation': lambda: calls.append(1)}}
    scroll = QWidget(window)
    scroll.setProperty('loaded', True)
    _populate_role_tab(window, scroll, 1)
    assert calls == [1]
    assert 'furniture_recalc_pending' not in window._official_role_editors[1]


def test_background_refresh_reads_furniture_without_discarding_other_draft(window, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QScrollArea, QVBoxLayout
    from src.features.official_role import role_shell
    settings = WorldBonusSettings(6., .016, 15.)
    dependencies = object()
    controller = SimpleNamespace(dependencies=dependencies,
        request_index=lambda apply, failed: apply(([], settings, ('new-source',))))
    window._official_role_controller = controller
    window.app_context = object()
    window.my_role_form_layout = QVBoxLayout(window)
    window.my_role_form_area = QScrollArea(window)
    window._my_role_dirty = True
    window._official_role_editors = {1: {'detail': {'profile': {'character_level': 42},
        'world_bonus': WorldBonusSettings().to_payload()}}}
    monkeypatch.setattr(role_shell, '_role_controller', lambda _: controller)
    monkeypatch.setattr(role_shell.OfficialRoleDependencies, 'from_app_context', lambda _: dependencies)
    role_shell._refresh_my_role(window)
    assert read_world_bonus_controls(window) == settings
    assert window._official_role_editors[1]['detail']['profile']['character_level'] == 42
