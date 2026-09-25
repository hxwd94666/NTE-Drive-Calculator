# 验证闲置连接释放不打断活跃功能，以及通信日志不携带用户正文。
import json
from threading import Event, Thread

import pytest

from src.integrations import native_transport_diagnostics as diagnostics
from tests.test_native_game_session import make_session


def test_idle_connection_closes_and_next_operation_reconnects():
    session, clients = make_session()
    session.inspect()
    assert session.close_if_idle()
    assert clients[0].closed
    assert not session.close_if_idle()
    session.inspect()
    assert len(clients) == 2
    session.close()


def test_idle_release_preserves_battle_and_hud():
    session, clients = make_session()
    battle = session.battle_client()
    assert not session.close_if_idle()
    assert not clients[0].closed
    battle.close()
    clients[0].hello_result["capabilities"].append("native_hud_v1")
    session.configure_hud({"cooldown": True, "enemy_bars": False}, connect=True)
    assert not session.close_if_idle()
    session.configure_hud({"cooldown": False, "enemy_bars": False}, connect=False)
    assert session.close_if_idle()


def test_idle_release_preserves_inventory_owner():
    session, clients = make_session()
    session.inspect()
    clients[0].hello_result["capabilities"].append("native_inventory_dto_v1")
    lease = session.inventory_client()
    assert not session.close_if_idle()
    lease.close()
    assert session.close_if_idle()


def test_idle_release_does_not_cancel_concurrent_read():
    session, clients = make_session()
    session.inspect()
    entered, release = Event(), Event()
    def read():
        with session._snapshot_scope(lambda: None):
            entered.set()
            release.wait(2)
    thread = Thread(target=read)
    thread.start()
    try:
        assert entered.wait(1)
        assert not session.close_if_idle()
        assert not clients[0].closed
    finally:
        release.set()
        thread.join(2)
        session.close()


def test_transport_diagnostics_never_include_response_text(monkeypatch):
    rows = []
    monkeypatch.setattr(diagnostics, "log_event", lambda *a, **kw: rows.append(kw))
    raw = '{"private_account":"sensitive-value"'
    with pytest.raises(json.JSONDecodeError) as error:
        json.loads(raw)
    diagnostics.invalid_json(raw, error.value, executable_sha256="a" * 64, exit_code=1)
    assert rows[0]["newline_complete"] is False
    assert rows[0]["line_chars"] == len(raw)
    assert "sensitive" not in str(rows)
    diagnostics.output_diagnostic('nte_core_output {"outcome":"timeout","elapsed_ms":5000,"bytes_total":6000,"bytes_written":4096,"body":"secret"}')
    assert rows[1]["bytes_written"] == 4096
    assert "secret" not in str(rows)
    diagnostics.output_diagnostic('nte_core_output {"outcome":"secret","elapsed_ms":1}')
    diagnostics.output_diagnostic('nte_core_output {"outcome":[]}')
    assert len(rows) == 2


def test_runtime_costs_are_bounded_and_omit_extra_fields(monkeypatch):
    rows = []
    monkeypatch.setattr(diagnostics, "log_event", lambda *a, **kw: rows.append(kw))
    counter = {"calls":2,"total_us":150,"max_us":100,"account":"secret"}
    status = {"native_status":{"runtimePerformance":{"version":1,"snapshot_pulse":counter,"snapshot_read":counter}}}
    logger = diagnostics.RuntimeCostLog()
    logger.observe(status)
    logger.observe(status)
    assert len(rows) == 1
    assert "secret" not in str(rows)


def test_incomplete_revision_backs_off_but_new_revision_can_progress():
    from src.services.native_snapshot_changes import NativeSnapshotChanges
    now = [0.0]
    changes = NativeSnapshotChanges(clock=lambda: now[0])
    row = {"domain":"inventory","revision":"1","ready":True,"dirty":True,"domainKey":"synthetic"}
    status = {"providerId":"synthetic", "domains":[row]}
    assert changes.needs_refresh(status, "inventory")
    for delay in (1, 2, 4, 8, 10, 10):
        assert changes.defer(status, "inventory") == delay
        assert not changes.needs_refresh(status, "inventory")
        now[0] += delay
        assert changes.needs_refresh(status, "inventory")
    changes.defer(status, "inventory")
    row["revision"] = "2"
    changes.needs_refresh(status, "inventory")
    now[0] += 0.3
    assert changes.needs_refresh(status, "inventory")
    changes.accept(status, "inventory")
    row["dirty"] = False
    assert not changes.needs_refresh(status, "inventory")


def test_notification_diagnostics_only_include_bounded_counters(monkeypatch):
    rows = []
    monkeypatch.setattr(diagnostics, "log_event", lambda *a, **kw: rows.append(kw))
    counter = {"calls": 0, "total_us": 0, "max_us": 0}
    notice = {"type": 1, "calls": 2, "items": 1, "empty": 1,
              "invalid": 0, "last_monotonic_ms": 100, "payload": "private"}
    costs = {"version": 1, "snapshot_pulse": counter, "snapshot_read": counter,
             "inventory_notifications": [notice, dict(notice, type=256), dict(notice, items=-1)]}
    diagnostics.RuntimeCostLog().observe({"native_status": {"runtimePerformance": costs}})
    notices = rows[0]["counters"]["inventory_notifications"]
    assert len(notices) == 1 and notices[0]["items"] == 1
    assert "private" not in str(rows)


def test_refresh_diagnostics_preserve_unknown_and_only_log_safe_fields(monkeypatch):
    rows = []
    monkeypatch.setattr(diagnostics, "log_event", lambda *a, **kw: rows.append(kw))
    diagnostics.snapshot_refresh_diagnostic({
        "ready": False, "dirty": "private", "recordCount": 1609, "sourceRecordCount": True,
        "missing": ["object_domain_not_ready", "private payload", {"secret": 1}],
        "domainKey": "private-key", "records": ["private record"],
        "collectionWork": {"version": 1, "steps": 400, "active_us": 1700,
                           "rows_read": 1, "rows_reused": 1608, "rows_removed": -1, "incremental": True,
                           "elapsed_us": -1, "max_step_us": True, "secret": "private"},
    }, {"domain": "inventory", "scope": "all_items"}, 20.25)
    assert len(rows) == 1
    row = rows[0]
    assert row["scope"] == "all_items" and row["ready"] is False
    assert row["collection_work"] == {"steps": 400, "active_us": 1700,
                                      "rows_read": 1, "rows_reused": 1608, "incremental": True}
    assert row["missing_count"] == 3 and row["missing_codes"] == ["object_domain_not_ready"]
    assert "private" not in str(row) and "sourceRecordCount" not in row and "dirty" not in row
    diagnostics.snapshot_refresh_diagnostic({}, {"domain": "character"}, 1)
    assert "collection_work" not in rows[1] and "ready" not in rows[1]


def test_inventory_source_change_waits_without_stopping_shared_core():
    from src.integrations.nte_core_protocol import NteCoreRpcError
    session, clients = make_session()
    session.inspect()
    client = clients[0]
    client.hello_result["capabilities"].append("native_inventory_dto_v1")
    def changed(method, _params):
        if method == "native.snapshot.refresh":
            raise NteCoreRpcError({"code": -32001, "message": "source_changed"})
    client.on_call = changed
    lease = session.inventory_client()
    try:
        lease.start_capture(profile="inventory")
        state = lease.status()
        assert state["capture_status"] == "running" and not state["native_snapshot_ready"]
        assert "自动重读" in state["message"]
        assert not client.closed and not client.aborted
    finally:
        lease.close()
        session.close()
