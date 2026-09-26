# 验证交互日志的敏感字段过滤、有界输入、限频与 DLL 重启去重。
from copy import deepcopy

import pytest

from src.integrations import native_hud_interaction as hud
from src.integrations import native_transport_diagnostics as transport


def payload():
    return dict(version=1, observed_monotonic_us=10000, hud_options=1, active_mask=0,
                dropped=0, latest_sequence=1, recent_available=True,
                stages={"hud": dict(calls=1, active=0, faults=0)},
                recent=[dict(sequence=1, at_monotonic_us=9000, duration_us=0, detail=3,
                             active_mask=8, hud_options=1, event="skill_selection", stage="hud",
                             input=0, candidates=2)])


def capture(monkeypatch):
    rows, now = [], [0.0]
    monkeypatch.setattr(hud, "monotonic", lambda: now[0])
    monkeypatch.setattr(hud, "log_event", lambda level, event, message, context, **kw: rows.append((event, kw)))
    return rows, now


def test_status_transport_and_no_synthetic_old_dll_fields(monkeypatch):
    rows, _ = capture(monkeypatch)
    monkeypatch.setattr(transport, "log_event", lambda *args, **kw: None)
    logger = transport.RuntimeCostLog()
    status = {"native_status": {"runtimePerformance": {"version": 1}}}
    logger.observe(status)
    assert rows == []
    status["native_status"]["runtimePerformance"]["hud_interaction"] = payload()
    logger.observe(status)
    assert rows[-1][1]["reason"] == "ui_call_mismatch"
    assert rows[-1][1]["input"] == "E"


def test_bounded_whitelist_and_restart(monkeypatch):
    rows, now = capture(monkeypatch)
    value, logger = payload(), hud.HudInteractionLog()
    value["uid"] = "private-body"
    value["recent"][0]["object"] = "private-object"
    value["stages"]["hud"]["path"] = "private-path"
    value["stages"]["private-stage"] = {}
    logger.observe(value)
    logger.observe(value)
    assert len(rows) == 2 and "private" not in str(rows)
    now[0] = 1
    logger.observe(value)
    assert len(rows) == 2
    now[0] = 2
    value["observed_monotonic_us"] += 100000
    value["recent"][0]["at_monotonic_us"] += 100000
    logger.observe(value)
    assert len(rows) == 3  # Same sequence in another DLL lifetime is not suppressed.


@pytest.mark.parametrize("bad", [True, -1, 2**63, "private", None])
def test_invalid_and_untrusted_event_fields(monkeypatch, bad):
    rows, _ = capture(monkeypatch)
    value = payload()
    value["recent"][0]["detail"] = bad
    hud.HudInteractionLog().observe(value)
    assert len(rows) == 1


@pytest.mark.parametrize("change", [dict(event=[]), dict(stage={}), dict(input=2), dict(candidates=2049),
                                    dict(detail=999), dict(at_monotonic_us=10001), dict(hud_options=32)])
def test_bad_selection_does_not_escape_parser(monkeypatch, change):
    rows, _ = capture(monkeypatch)
    value = payload()
    value["recent"][0].update(change)
    hud.HudInteractionLog().observe(value)
    assert len(rows) == 1


def test_unavailable_and_oversized_ring(monkeypatch):
    rows, now = capture(monkeypatch)
    value, logger = payload(), hud.HudInteractionLog()
    value["recent_available"] = False
    logger.observe(value)
    now[0] = 1
    value["recent_available"] = True
    value["recent"] *= 33
    logger.observe(value)
    assert len(rows) == 1
    now[0] = 2
    value = deepcopy(payload())
    value["recent"][0].update(event="fault", detail=0xC0000005)
    value["stages"]["hud"]["faults"] = 1
    logger.observe(value)
    assert len(rows) == 3 and rows[-1][1]["detail"] == 0xC0000005
