# 验证同场战报的 DPS 投影不会被状态通知切回原始计时口径。
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from src.domain.battle_report import (
    BattleAbyssHalfSummary, BattleAbyssSummary, BattleCaptureState,
    BattleQualitySummary, BattleSummary,
)
from src.features.battle_report.overlay import BattleReportOverlay
from src.features.battle_report.page import BattleReportPage
from src.features.battle_report.summary_clock import summary_clock_label


def _summary() -> BattleSummary:
    return BattleSummary(
        duration_seconds=10, dps_time_mode="wall_clock", total_damage=1000,
        total_dps=100, total_damage_taken=0, total_hits=1, characters=(), skills=(),
        abyss=BattleAbyssSummary(), quality=BattleQualitySummary(),
    )


@pytest.fixture
def page():
    app = QApplication.instance() or QApplication([])
    view = BattleReportPage(game_ui_asset_root="data/game_ui")
    view.long_analysis_view.set_analysis = Mock()
    view.marginal_page.set_source_analysis = Mock()
    yield view
    view.close()
    assert app is not None


def _analyze(page):
    page.set_analysis(SimpleNamespace(
        axis_complete=True, timeline_damage_correction_total=0,
        battle_start_us=0, battle_end_us=10_000_000,
        time_stop_intervals=((2_000_000, 4_000_000),),
        time_stop_source_kind="nte_core",
    ))


def test_same_record_status_refresh_keeps_analyzed_dps(page):
    state = BattleCaptureState("history", "历史战报", False, _summary(), battle_record_id=7)
    page.update_state(state)
    assert page.metric_subtitles["dps"].text() == "真实时间"
    _analyze(page)
    assert page.metric_labels["dps"].text() == "125"
    page.update_state(replace(state, message="已保存", retention_kind="manual"))
    assert page.metric_labels["dps"].text() == "125"
    assert page.metric_labels["duration"].text() == "8.0s（10.0s）"
    assert page.metric_labels["damage"].text() == "1,000"
    assert page.metric_subtitles["dps"].text() == "有效时间"


def test_equal_summary_from_another_record_does_not_reuse_old_projection(page):
    state = BattleCaptureState("history", "历史战报", False, _summary(), battle_record_id=7)
    page.update_state(state)
    _analyze(page)
    page.update_state(replace(state, battle_record_id=8))
    assert page.metric_labels["dps"].text() == "100"
    assert page._source_analysis is None
    assert page.metric_subtitles["dps"].text() == "真实时间"


def test_new_live_summary_uses_its_own_damage_and_clock(page):
    state = BattleCaptureState("running", "采集中", True, _summary())
    page.update_state(state)
    _analyze(page)
    updated = replace(state.summary, sequence=2, total_damage=1500, total_dps=150)
    page.update_state(replace(state, summary=updated))
    assert page.metric_labels["dps"].text() == "150"
    assert page.metric_labels["damage"].text() == "1,500"
    assert page._source_analysis is None


def test_overlay_curve_never_mixes_clock_or_abyss_half(page):
    overlay = BattleReportOverlay(game_ui_asset_root="data/game_ui")
    summary = _summary()
    overlay.update_summary(summary)
    overlay.update_summary(replace(summary, sequence=2, total_dps=101))
    assert tuple(overlay._history) == (100, 101)
    summary = replace(summary, dps_time_mode="subtract_time_stop", total_dps=125)
    overlay.update_summary(summary)
    assert tuple(overlay._history) == (125,)
    first = BattleAbyssHalfSummary("first", 10, 1000, 100, (), ())
    second = BattleAbyssHalfSummary("second", 2, 600, 300, (), ())
    overlay.update_summary(replace(summary, abyss=BattleAbyssSummary(True, active_half="first", first_half=first)))
    overlay.update_summary(replace(summary, abyss=BattleAbyssSummary(True, active_half="second", first_half=first, second_half=second)))
    assert tuple(overlay._history) == (300,)
    overlay.close()


def test_unknown_clock_is_not_labeled_active_time():
    assert summary_clock_label("future_clock") == "计时未知"


def test_partial_observed_clock_keeps_wall_time_dps(page):
    page.update_state(BattleCaptureState("history", "历史战报", False, _summary(), battle_record_id=7))
    analysis = SimpleNamespace(
        axis_complete=True, timeline_damage_correction_total=0,
        battle_start_us=0, battle_end_us=10_000_000,
        observed_time_stop_intervals=((2_000_000, 4_000_000),),
        time_stop_intervals=((2_000_000, 4_000_000),),
        time_stop_source_kind="nte_core_partial",
    )
    page.set_analysis(analysis)
    assert page.metric_labels["dps"].text() == "100"
    assert "真实时间" in page.metric_subtitles["dps"].text()
    assert "未扣时停" in page.metric_subtitles["duration"].text()
    assert analysis.observed_time_stop_intervals == ((2_000_000, 4_000_000),)
    from src.features.battle_report.timeline_layout import format_time_stop_evidence
    assert "覆盖不完整" in format_time_stop_evidence(analysis)


def test_mixed_observed_and_inferred_stops_restore_only_already_subtracted_time(page):
    summary = replace(_summary(), duration_seconds=8, dps_time_mode="subtract_time_stop", total_dps=125)
    page.update_state(BattleCaptureState("history", "历史战报", False, summary, battle_record_id=7))
    page.set_analysis(SimpleNamespace(
        axis_complete=True, timeline_damage_correction_total=0,
        battle_start_us=0, battle_end_us=7_000_000,
        observed_time_stop_intervals=((2_000_000, 4_000_000),),
        time_stop_intervals=((2_000_000, 4_000_000), (5_000_000, 6_000_000)),
        time_stop_source_kind="nte_core_with_inferred_linko_e",
    ))
    assert page.metric_labels["duration"].text() == "7.0s（10.0s）"
    assert page.metric_labels["dps"].text() == "143"
    assert page.metric_labels["damage"].text() == "1,000"
