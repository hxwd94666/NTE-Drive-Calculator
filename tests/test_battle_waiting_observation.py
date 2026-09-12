# 验证空摘要维持等待，真实零伤命中可见，切换半场与重录清空旧曲线。
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication

from src.domain.battle_report import BattleAbyssHalfSummary, BattleAbyssSummary
from src.features.battle_report.overlay import BattleReportOverlay
from src.integrations.nte_core_battle import parse_battle_summary
from src.observability import OperationContext
from src.services.battle_capture_service import BattleCaptureService


@pytest.fixture
def overlay():
    app = QApplication.instance() or QApplication([])
    widget = BattleReportOverlay(game_ui_asset_root="data/game_ui")
    yield widget
    widget.close()
    app.processEvents()


def test_context_only_summary_waits_even_with_elapsed_time_and_packets(overlay):
    empty = parse_battle_summary({"duration_seconds": 20, "quality": {"packet_count": 5}})
    overlay.update_summary(empty)
    assert overlay._summary is None
    assert not overlay._history


@pytest.mark.parametrize("payload", [
    {"total_hits": 1}, {"total_damage_taken": 1}, {"quality": {"incoming_hits": 1}},
    {"quality": {"unknown_direction_hits": 1}}, {"total_damage": 1},
])
def test_actual_hit_or_incoming_evidence_is_not_hidden(overlay, payload):
    summary = parse_battle_summary(payload)
    overlay.update_summary(summary)
    assert overlay._summary == summary
    assert len(overlay._history) == 1


def test_reset_empty_summary_clears_previous_dps(overlay):
    overlay.update_summary(parse_battle_summary({"total_hits": 1, "total_damage": 10, "total_dps": 10}))
    overlay.update_summary(parse_battle_summary({"sequence": 2}))
    assert overlay._summary is None
    assert not overlay._history


def test_lower_half_waits_without_borrowing_upper_half_damage(overlay):
    upper = BattleAbyssHalfSummary("first", 10, 100, 10, (), ())
    summary = parse_battle_summary({"total_hits": 5, "total_damage": 100})
    summary = replace(summary, abyss=BattleAbyssSummary(True, active_half="first", first_half=upper))
    overlay.update_summary(summary)
    for lower in (None, BattleAbyssHalfSummary("second", 0, 0, 0, (), ())):
        overlay.update_summary(replace(summary, abyss=replace(summary.abyss, active_half="second", second_half=lower)))
        assert overlay._summary is None
        assert not overlay._history


def test_empty_summary_keeps_service_listening_without_claiming_damage():
    service = BattleCaptureService(client_factory=lambda: None, operation_context=OperationContext.create("battle_report"))
    service._on_summary_event({"method": "event.battle.summary", "params": {"sequence": 1}})
    assert service.state.phase == "running"
    assert "等待战斗数据" in service.state.message
    assert service.state.summary.total_hits == 0
    assert not service._stop_event.is_set()
    service._on_summary_event({"method": "event.battle.summary", "params": {"sequence": 2, "total_hits": 1}})
    assert "已收到实时战斗数据" in service.state.message


def test_failed_start_is_not_logged_as_ready(monkeypatch):
    events = []
    monkeypatch.setattr("src.services.battle_capture_service.log_event", lambda *args, **kwargs: events.append((args[1], kwargs)))

    def fail():
        raise RuntimeError("synthetic startup failure")

    service = BattleCaptureService(client_factory=fail, required_source="native",
                                   operation_context=OperationContext.create("battle_report"))
    service._run()
    assert events[0][0] == "battle_report.capture_starting"
    assert events[0][1]["source"] == "native"
    assert not any(event == "battle_report.capture_started" for event, _ in events)
