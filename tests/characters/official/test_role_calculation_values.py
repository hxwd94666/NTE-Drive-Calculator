# 保护后台角色计算、最新草稿回执及界面只消费完整数值；不读取个人账号或连接游戏。
from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import replace
import threading
import time
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QVBoxLayout, QWidget

from src.features.official_role.controller import OfficialRoleController
from src.features.official_role.dependencies import OfficialRoleDependencies
from src.features.official_role.role_shell import _render_role_tab
from src.features.official_role.role_calculation import _calculation_detail
from src.integrations.analysis_core.nte_analysis_core import NteAnalysisCoreClient, NativeAnalysisError
from src.services.official_role.calculation_values import freeze_role_calculation_detail, prepare_role_calculation_values
from src.services.official_role.native_damage import role_damage_calculator
from src.services.official_role.page import load_official_role_detail
from src.services.official_role.panel.attributes import _role_panel_damage_inputs
from src.services.official_role.equipment.marginal_scoring import calculate_official_role_damage_batch
from src.ui.common.text_catalog import resolve_text_catalog
from tests.allocation.test_axis_postprocess_component import axis_facade_case, ROOT

NTE_TEST_TIER = 'core'


@pytest.fixture(scope='module')
def runtime_case(tmp_path_factory):
    case = axis_facade_case(tmp_path_factory.mktemp('role-calculation-values'), second=True, joint=True)
    deps = case['dependencies']
    detail = load_official_role_detail(deps.user_database_path, 1003,
        static_database_path=deps.static_database_path, include_replacement_candidates=False)
    return case, detail


def test_prepared_values_are_exact_bounded_and_never_reenter_transport_or_reference(runtime_case):
    case, detail = runtime_case
    frozen = freeze_role_calculation_detail(detail)
    values = prepare_role_calculation_values(frozen, 'current')
    assert values and 0 < len(values.values) <= 4096
    projected = {**frozen, '_role_damage_values': values}
    with patch.object(NteAnalysisCoreClient, '_run', side_effect=AssertionError('GUI must not send requests')), \
            patch('src.services.official_role.native_damage.DamageCalculationService.calculate_direct', side_effect=AssertionError('no reference fallback')):
        with role_damage_calculator(projected) as calculator:
            inputs = _role_panel_damage_inputs(projected, 'current')
            assert calculator.calculate(inputs) == tuple(dict(values.values)[row] for row in inputs)
            with pytest.raises(NativeAnalysisError):
                calculator.calculate((replace(inputs[0], attack_add=inputs[0].attack_add + 123.456),))
    changed = freeze_role_calculation_detail(projected)
    changed['profile']['character_level'] = 1
    assert detail['profile']['character_level'] != 1 and '_role_damage_values' not in changed
    def cancelled():
        raise CancelledError
    with pytest.raises(CancelledError):
        prepare_role_calculation_values(frozen, 'current', checkpoint=cancelled)


def wait(app, condition, seconds=15):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.001)
    assert condition()


def test_real_page_and_changed_draft_keep_native_transport_off_gui_thread(runtime_case, monkeypatch):
    case, _ = runtime_case
    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    scroll = QScrollArea(owner)
    QVBoxLayout(owner).addWidget(scroll)
    deps = case['dependencies']
    controller = OfficialRoleController(OfficialRoleDependencies(deps.account_id, deps.generation,
        deps.user_database_path, deps.static_database_path, ROOT / 'data/app_shared.sqlite3'), owner)
    owner._official_role_controller = controller
    owner._official_role_editors = {}
    owner._official_role_dirty_ids = set()
    owner._my_role_dirty = False
    gui = threading.get_ident()
    calls = []
    original = NteAnalysisCoreClient._run
    def transport(client, *args, **kwargs):
        assert threading.get_ident() != gui
        calls.append(threading.get_ident())
        return original(client, *args, **kwargs)
    monkeypatch.setattr(NteAnalysisCoreClient, '_run', transport)
    errors = []
    def apply(result):
        detail, source = result
        detail['_view_source_key'] = source
        _render_role_tab(owner, scroll, 1003, detail)
    try:
        controller.request_detail(1003, apply, errors.append)
        wait(app, lambda: bool(scroll.property('loaded')) or bool(errors))
        assert not errors
        wait(app, lambda: not controller.is_loading())
        editor = owner._official_role_editors[1003]
        original_values = editor['detail']['_role_damage_values']
        checkbox = editor['likeability_level_10']
        checkbox.setChecked(not checkbox.isChecked())
        assert editor['calculation_pending']
        checkbox.setChecked(not checkbox.isChecked())
        checkbox.setChecked(not checkbox.isChecked())
        wait(app, lambda: not editor.get('calculation_pending') and not controller.is_loading())
        assert editor['detail']['_role_damage_values'] != original_values
        # 好感变化同样影响弧盘的两侧伤害；不能只刷新主面板而遗留旧收益提示。
        projected = _calculation_detail(editor['detail'], editor)
        without_fork = {**projected, 'profile': {**projected['profile'], 'fork_id': None}}
        current, baseline = calculate_official_role_damage_batch((projected, without_fork),
            editor['equipment_context_key'])
        assert current is not None and baseline and baseline > 0
        expected = resolve_text_catalog(None).render('ui.official_role.refresh_fork_summary.margin_label',
            gain=f'{(current / baseline - 1.0) * 100:+.2f}')
        fork_group = owner.findChild(QWidget, 'officialRoleForkGroup')
        assert any(label.text() == expected for label in fork_group.findChildren(QLabel))
        if editor['fork'].count() > 1:
            editor['fork'].setCurrentIndex(1)
            wait(app, lambda: not editor.get('calculation_pending') and not controller.is_loading())
            editor['fork_level'].setValue(max(1, editor['fork_level'].value() - 1))
            wait(app, lambda: not editor.get('calculation_pending') and not controller.is_loading())
            editor['refinement'].setCurrentIndex(3)
            wait(app, lambda: not editor.get('calculation_pending') and not controller.is_loading())
        assert calls
    finally:
        controller.close()
        wait(app, lambda: not controller.is_loading())
        owner.close()
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


def test_controller_discards_cancelled_calculation_and_allows_retry(runtime_case):
    case, detail = runtime_case
    app = QApplication.instance() or QApplication([])
    deps = case['dependencies']
    controller = OfficialRoleController(OfficialRoleDependencies(deps.account_id, deps.generation,
        deps.user_database_path, deps.static_database_path, ROOT / 'data/app_shared.sqlite3'))
    entered, release = threading.Event(), threading.Event()
    delivered, failed, discarded = [], [], []
    original = prepare_role_calculation_values
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    try:
        with patch('src.services.official_role.calculation_values.prepare_role_calculation_values', delayed):
            controller.request_calculation(detail, 'current', {}, {}, delivered.append, failed.append, lambda: discarded.append(True))
            wait(app, entered.is_set)
            controller.cancel_reads()
            release.set()
            wait(app, lambda: not controller.is_loading())
        assert delivered == failed == [] and discarded == [True]
        controller.request_calculation(detail, 'current', {}, {}, delivered.append, failed.append)
        wait(app, lambda: not controller.is_loading())
        assert len(delivered) == 1 and not failed
    finally:
        release.set()
        controller.close()
        wait(app, lambda: not controller.is_loading())
        controller.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
