# 验证空摘要维持等待，真实零伤命中可见，切换半场与重录清空旧曲线。
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication

from src.domain.battle_report.capture.summary import BattleAbyssHalfSummary, BattleAbyssSummary
from src.features.battle_report.capture.overlay import BattleReportOverlay
from src.integrations.nte_core_battle import parse_battle_summary
from src.observability import OperationContext
from src.services.battle_report.capture.service import BattleCaptureService


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
    monkeypatch.setattr("src.services.battle_report.capture.service.log_event", lambda *args, **kwargs: events.append((args[1], kwargs)))

    def fail():
        raise RuntimeError("synthetic startup failure")

    service = BattleCaptureService(client_factory=fail, required_source="native",
                                   operation_context=OperationContext.create("battle_report"))
    service._run()
    assert events[0][0] == "battle_report.capture_starting"
    assert events[0][1]["source"] == "native"
    assert not any(event == "battle_report.capture_started" for event, _ in events)


@pytest.mark.parametrize("active_half", [None, "", "future_stage"])
def test_unconfirmed_abyss_half_waits_without_reusing_total_observation(overlay, active_half):
    # 正式摘要回调仍保留累计事实；活动半场未知时只撤下实时展示，不停止采集。
    service = BattleCaptureService(
        client_factory=lambda: pytest.fail("summary callback must not start a client"),
        operation_context=OperationContext.create("battle_report"),
    )
    service.add_state_handler(lambda state: overlay.update_summary(state.summary))
    upper = {"half": "first", "duration_seconds": 10, "total_damage": 100, "total_dps": 10}
    payload = {"total_hits": 5, "total_damage": 100, "total_dps": 10,
               "abyss": {"detected": True, "active_half": "first", "first_half": upper}}
    service._on_summary_event({"method": "event.battle.summary", "params": {**payload, "sequence": 1}})
    frozen = service.state.summary
    assert tuple(overlay._history) == (10.0,)

    payload["abyss"] = {**payload["abyss"], "active_half": active_half}
    service._on_summary_event({"method": "event.battle.summary", "params": {**payload, "sequence": 2}})

    assert service.state.phase == "running"
    assert "等待战斗数据" in service.state.message
    assert service.state.summary.abyss.active_half == active_half
    assert service.state.summary.total_damage == 100.0
    assert service.state.summary.abyss.first_half.total_damage == 100.0
    assert frozen.abyss.active_half == "first" and frozen.total_damage == 100.0
    assert not service._stop_event.is_set()
    assert overlay._summary is None
    assert not overlay._history

    # 下半场已确认的真实零伤命中仍可见，不把本次等待解释为整个报告无伤害。
    payload["abyss"] = {**payload["abyss"], "active_half": "second", "second_half": {
        "half": "second", "characters": [{"char_id": 1001, "name": "角色", "hits": 1}],
    }}
    service._on_summary_event({"method": "event.battle.summary", "params": {**payload, "sequence": 3}})
    assert "已收到实时战斗数据" in service.state.message
    assert overlay._summary is service.state.summary
    assert tuple(overlay._history) == (0.0,)
    assert service.state.summary.total_damage == 100.0
