# 验证两条产品入口共用明确轴选择器且展示目标与截断语义，不读取账号或执行搜索。
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QButtonGroup, QLabel, QPushButton, QRadioButton, QToolButton, QWidget
import pytest

from src.features.allocation.damage_result_summary import damage_optimization_presentation, damage_optimization_summary
from src.ui.damage_optimization_panel import damage_optimization_panel
from src.features.battle_report.controller import BattleReportController
from src.features.battle_report.page import BattleReportPage
from src.features.scanning.controller import ScanningController
from src.services.battle_report.analysis.load import BattleReportAnalysisLoadRequest


NTE_TEST_TIER = "core"


@pytest.fixture
def ui():
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    yield app, parent
    parent.close()
    parent.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def test_marginal_keyboard_action_emits_one_allocation_request_not_recalculation(ui):
    app, parent = ui
    page = BattleReportPage(game_ui_asset_root=Path(__file__).resolve().parents[2] / "data/game_ui_assets", parent=parent)
    requested, recalculated = Mock(), Mock()
    page.axis_allocation_requested.connect(requested)
    page.marginal_recalculate_requested.connect(recalculated)
    page.show_marginal({}, request_automatic_recalculation=False)
    parent.show()
    page.show()
    app.processEvents()
    button = next(button for button in page.findChildren(QPushButton) if button.accessibleName() == "按本轴优化装备")
    assert button.isVisible() and button.isEnabled()
    button.setFocus()
    QTest.keyClick(button, Qt.Key_Space)
    app.processEvents()
    requested.assert_called_once_with()
    recalculated.assert_not_called()


def test_battle_controller_handoff_preserves_explicit_selector_but_excludes_marginal_draft():
    callback = Mock()
    base = BattleReportAnalysisLoadRequest(7, start_us=500_000, end_us=1_000_001, detail_level="marginal",
        selected_character_id=1003, marginal_candidate=object(), marginal_benefit_candidate=object(),
        marginal_drive_units=(("AtkUp", 100.0),))
    owner = SimpleNamespace(_axis_allocation_requested=callback, _dialog_parent=None, _latest_state=SimpleNamespace(battle_record_id=7),
        _latest_analysis_load_request=base, _page=SimpleNamespace(marginal_page=SimpleNamespace(selected_character_id=lambda: 1072),
        marginal_detail_scope=lambda: None))
    BattleReportController._request_axis_allocation(owner)
    sent = callback.call_args.args[0]
    assert (sent.battle_record_id, sent.start_us, sent.end_us, sent.selected_character_id) == (7, 500_000, 1_000_001, 1072)
    assert sent.detail_level == "hit" and sent.marginal_candidate is None and sent.marginal_benefit_candidate is None
    assert sent.marginal_drive_units is None and base.marginal_drive_units is not None
    owner._latest_state.battle_record_id = None
    with patch("src.features.battle_report.controller.QMessageBox.warning") as warning:
        BattleReportController._request_axis_allocation(owner)
    assert callback.call_count == 1
    warning.assert_called_once()


def test_scanning_entry_navigates_and_queues_same_service_without_simulated_click(ui):
    _app, parent = ui
    scan, strategy = QButtonGroup(parent), QButtonGroup(parent)
    for group, ids in ((scan, (1, 4)), (strategy, (0, 3))):
        for identifier in ids:
            group.addButton(QRadioButton(parent), identifier)
    clicked = Mock()
    scan.button(4).clicked.connect(clicked)
    strategy.button(3).clicked.connect(clicked)
    callbacks = []
    owner = SimpleNamespace(is_running=lambda: False, build_page=Mock(), navigate=Mock(), dialog_parent=parent,
        scan_group=scan, strategy_group=strategy, role_selector=SimpleNamespace(get_selected=lambda: ["灵可"]),
        _prepare_calculation=callbacks.append)
    request = BattleReportAnalysisLoadRequest(7, selected_character_id=1072)
    with patch("src.features.scanning.controller._do_exec") as execute:
        ScanningController.start_axis_allocation_from_battle(owner, request)
        execute.assert_not_called()
        callbacks[0]()
    owner.navigate.assert_called_once_with("execute")
    assert scan.checkedId() == 4 and strategy.checkedId() == 3
    execute.assert_called_once_with(owner, axis_initial_request=request)
    clicked.assert_not_called()


def test_summary_distinguishes_shared_axis_direct_group_and_each_budget():
    value = {"status": "completed", "baseline_value": 100, "candidate_value": 110, "evaluations": 3,
        "objective": "frozen_axis_expected_v1", "neighborhood": "single_and_pair_fixed_signature",
        "termination": "local_optimum", "neighborhood_complete": True,
        "shared_value": True, "scope": {"source_axis_complete": False}}
    presentation = damage_optimization_presentation(value)
    assert "全队轴伤估算 +10.00%" == presentation.headline
    assert "角色优先" in presentation.note and "所选战报事件" in presentation.details
    assert "请勿相加" in presentation.details and "整队配装" in presentation.details
    assert "双件" in presentation.details and "组件" not in presentation.details
    assert "伤害计算" not in damage_optimization_summary(value)
    value["scope"].update(excluded_unscoped_event_count=1, settlement_profile_character_count=4)
    presentation = damage_optimization_presentation(value)
    assert "排除 1 击" in presentation.headline and "原战报保留" in presentation.details
    assert "结算时冻结" in presentation.details
    pending = damage_optimization_presentation({**value, "status": "not_ready"})
    assert "排除 1 击" in pending.details
    value.update(objective="standard_direct_v1", group_objective_value=.1, neighborhood="single_and_pair")
    presentation = damage_optimization_presentation(value)
    assert "标准直伤指标" in presentation.headline and "实战" in presentation.details
    assert "卡带按满级" in presentation.details and "同级组平均提升" not in presentation.details
    for termination, label in (("evaluation_budget", "伤害计算次数上限"), ("round_budget", "优化轮数上限"),
                               ("enumeration_budget", "候选遍历上限"), ("model_gap", "部分候选")):
        value.update(termination=termination, neighborhood_complete=False)
        presentation = damage_optimization_presentation(value)
        assert "搜索未完成" in presentation.headline and label in presentation.details
    value.update(status="budget_limited", reason="首轮尚未完整")
    presentation = damage_optimization_presentation(value)
    assert "搜索未完成" in presentation.headline and "角色优先重新计算" in presentation.details
    assert "尚未生成可保存" in presentation.note


@pytest.mark.parametrize("before,after,expected", [
    (0, 1, "+1.00"), (100, 100, "本轮未找到提升"),
    (100, 100.0001, "不足 0.01%"), (float("nan"), 100, "结果待核对"),
    (100, 99, "结果待核对"), (10**400, 100, "结果待核对"),
])
def test_summary_zero_tiny_or_invalid_gain_never_claims_a_false_improvement(before, after, expected):
    value = {"objective": "standard_direct_v1", "status": "completed", "baseline_value": before,
             "candidate_value": after, "termination": "local_optimum", "neighborhood_complete": True}
    summary = damage_optimization_summary(value)
    assert expected in summary
    assert "nan" not in summary.lower() and "inf" not in summary.lower()


def test_summary_requires_coverage_evidence_and_does_not_reinterpret_unknown_objective():
    value = {"objective": "standard_direct_v1", "status": "completed", "baseline_value": 100,
             "candidate_value": 100, "termination": "local_optimum"}
    assert "搜索未完成" in damage_optimization_summary(value)
    value["objective"] = "future_model"
    assert "目标类型未知" in damage_optimization_summary(value)
    assert damage_optimization_summary({}) == ""


def test_summary_details_keyboard_toggle_keeps_primary_conclusion_visible(ui):
    app, parent = ui
    value = {"objective": "standard_direct_v1", "status": "completed", "baseline_value": 100,
             "candidate_value": 110, "termination": "local_optimum", "neighborhood_complete": True}
    panel = damage_optimization_panel(value, object_name="testDamageOptimization", parent=parent)
    parent.resize(600, 600)
    panel.resize(550, 550)
    parent.show()
    app.processEvents()
    button = panel.findChild(QToolButton)
    labels = panel.findChildren(QLabel)
    details = next(label for label in labels if "对比本次角色优先" in label.text())
    assert details.isHidden()
    assert button.isVisible()
    assert "对比本次角色优先" in details.text()
    button.setFocus()
    QTest.keyClick(button, Qt.Key_Space)
    app.processEvents()
    assert not details.isHidden() and button.accessibleName() == "收起优化详情"
    assert any(label.isVisible() and "+10.00%" in label.text() for label in labels)
    QTest.keyClick(button, Qt.Key_Space)
    app.processEvents()
    assert details.isHidden() and button.accessibleName() == "查看优化详情"


@pytest.mark.parametrize("reason", ["axis_crit_fixed_projection_mismatch", "axis_crit_fork_growth_missing", "axis_crit_likeability_unknown"])
def test_unready_reason_leads_with_readable_guidance_not_internal_code(reason):
    value = {"objective": "frozen_axis_expected_v1", "status": "not_ready", "reason": reason}
    presentation = damage_optimization_presentation(value)
    assert reason not in presentation.note and ("请检查" in presentation.note or "请核对" in presentation.note)
    assert reason not in presentation.details and "角色优先重新计算" in presentation.details


def test_historical_large_or_malformed_metadata_does_not_break_result_page():
    value = {"objective": "standard_direct_v1", "status": "completed", "baseline_value": 1,
             "candidate_value": 1e308, "termination": [], "neighborhood": [], "group_objective_value": 1e308}
    presentation = damage_optimization_presentation(value)
    assert "inf" not in (presentation.headline + presentation.details).lower()
    assert "搜索未完成" in presentation.headline
    assert len(presentation.headline) < 80
    value.update(objective=[], status="completed")
    assert "目标类型未知" in damage_optimization_presentation(value).note


def test_expanded_neighborhood_and_time_budget_are_explained_without_fixed_layout_claim():
    from src.features.allocation.damage_result_summary import damage_optimization_presentation
    value = {"objective": "standard_direct_v1", "status": "completed", "baseline_value": 100.0,
             "candidate_value": 110.0, "termination": "local_optimum", "neighborhood_complete": True,
             "exchange_complete": True, "blueprint_complete": True, "neighborhood": "single_pair_exchange_blueprint"}
    result = damage_optimization_presentation(value)
    assert "换图纸" in result.details and "固定布局" not in result.details
    value.update(termination="time_budget", neighborhood_complete=False)
    result = damage_optimization_presentation(value)
    assert "时长上限" in result.details and "搜索未完成" in result.headline
