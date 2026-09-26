# 验证快照分段诊断的限频、重启去重和敏感字段过滤。
from copy import deepcopy

import pytest

from src.integrations import native_snapshot_timing as timing
from src.integrations import native_transport_diagnostics as transport


def payload():
    counter = dict(calls=2, total_us=25000, max_us=22000, over_8333_us=1, over_20000_us=1)
    sample = dict(sequence=1, started_monotonic_us=1000, total_us=24000,
                  job=1, step=2, domain="character", all_items=False, stages={"skills": counter})
    return dict(version=1, slow_threshold_us=20000, slow_count=1, dropped=0,
                recent_available=True, stages={"skills": counter}, recent=[sample])


def capture(monkeypatch):
    rows, now = [], [0.0]
    monkeypatch.setattr(timing, "log_event", lambda level, event, message, context, **kw: rows.append((event, kw)))
    monkeypatch.setattr(timing, "monotonic", lambda: now[0])
    return rows, now


def test_old_dll_optional_diagnostics_and_status_transport(monkeypatch):
    rows, _ = capture(monkeypatch)
    monkeypatch.setattr(transport, "log_event", lambda *args, **kwargs: None)
    counter = dict(calls=0, total_us=0, max_us=0)
    status = {"native_status": {"runtimePerformance": {
        "version": 1, "snapshot_pulse": counter, "snapshot_read": counter,
    }}}
    logger = transport.RuntimeCostLog()
    logger.observe(status)
    assert rows == []
    status["native_status"]["runtimePerformance"]["snapshot_diagnostics"] = payload()
    logger.observe(status)
    assert [event for event, _ in rows] == ["native_sync.timing_summary", "native_sync.slow_pulse"]


def test_rate_limit_dedup_and_sequence_restart(monkeypatch):
    rows, now = capture(monkeypatch)
    logger, value = timing.SnapshotTimingLog(), payload()
    logger.observe(value)
    logger.observe(value)
    assert len(rows) == 2
    now[0] = 30
    logger.observe(value)
    assert len(rows) == 2
    now[0] = 60
    value["recent"][0]["started_monotonic_us"] += 1000000
    logger.observe(value)
    assert len(rows) == 3 and rows[-1][0] == "native_sync.slow_pulse"


def test_only_whitelisted_stages_and_metadata_reach_logs(monkeypatch):
    rows, _ = capture(monkeypatch)
    value = payload()
    value.update(providerId="private-provider", body="private-body")
    value["stages"]["private-stage"] = deepcopy(value["stages"]["skills"])
    value["stages"]["skills"]["uid"] = "private-uid"
    value["recent"][0].update(uid="private-uid", path="private-path")
    value["recent"][0]["stages"]["private-stage"] = {}
    timing.SnapshotTimingLog().observe(value)
    assert len(rows) == 2 and "private" not in str(rows)
    assert set(rows[0][1]["stages"]) == {"skills"}
    assert rows[1][1]["job"] == 1 and rows[1][1]["step"] == 2


@pytest.mark.parametrize("bad", [True, -1, 2**63, "private", None])
def test_invalid_numeric_fields_are_not_logged(monkeypatch, bad):
    rows, _ = capture(monkeypatch)
    value = payload()
    value["recent"][0]["job"] = bad
    value["stages"]["skills"]["calls"] = bad
    timing.SnapshotTimingLog().observe(value)
    assert len(rows) == 1 and rows[0][1]["stages"] == {}


def test_unavailable_and_oversized_sample_buffers_are_not_logged(monkeypatch):
    rows, now = capture(monkeypatch)
    value, logger = payload(), timing.SnapshotTimingLog()
    value["recent_available"] = False
    logger.observe(value)
    assert len(rows) == 1
    now[0] = 30
    value["recent_available"] = True
    value["recent"] *= 10
    logger.observe(value)
    assert len(rows) == 1


def test_refresh_job_identity_is_optional_and_bounded(monkeypatch):
    rows = []
    monkeypatch.setattr(transport, "log_event", lambda *args, **kw: rows.append(kw))
    transport.snapshot_refresh_diagnostic({"diagnosticJob": 7, "diagnosticStep": 3,
                                           "private": "body"}, {"domain": "character"}, 1)
    assert rows[0]["diagnostic_job"] == 7 and rows[0]["diagnostic_step"] == 3
    transport.snapshot_refresh_diagnostic({"diagnosticJob": True, "diagnosticStep": -1},
                                          {"domain": "character"}, 1)
    assert "diagnostic_job" not in rows[1] and "diagnostic_step" not in rows[1]
    assert "private" not in str(rows)
