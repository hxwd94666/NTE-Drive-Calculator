# 验证入场预备、首击引用优先级、重开与冻结副本的生命周期。
from copy import deepcopy
from unittest.mock import patch

from src.services.native_battle_preparation import NativeBattlePreparation
from src.services.native_battle_scopes import validate_first_hit
from tests.test_native_battle_scopes import attempt
from tests.test_battle_capture_build_freeze import native_snapshot


def waiting(revision="1"):
    return {"native_capture": {"providerId": "p", "scopeAttempts": {},
            "cloneAttempt": {"revision": revision}, "contextEvents": [{"kind": "combat_context",
            "roots": {"world": "1:1", "controller": "2:1", "ps": "3:1"},
            "snapshotChanges": {d: {"revision": revision, "dirty": True}
                                for d in ("character", "inventory", "team", "environment")}}]}}


def test_prepares_before_damage_reuses_one_read_and_reprepares_on_restart():
    prepared = NativeBattlePreparation()
    reads = []
    def read():
        reads.append(1)
        return native_snapshot()
    record = waiting()
    prepared.prepare(record, {}, read)
    prepared.prepare(record, {}, read)
    assert len(reads) == 1
    frozen = prepared.take_matching(attempt(), record)
    assert frozen is not None
    frozen["domains"].clear()
    assert len(prepared.snapshot["domains"]) == 4
    assert prepared.take_matching(attempt(revision="2"), record) is None
    prepared.prepare(waiting("2"), {}, read)
    assert len(reads) == 2


def test_missing_context_does_not_poll_but_active_scope_keeps_baseline_current():
    prepared = NativeBattlePreparation()
    def forbidden():
        raise AssertionError("unexpected read")
    prepared.prepare({"native_capture": {}}, {}, forbidden)
    with patch("src.services.native_battle_preparation.monotonic", return_value=0):
        prepared.prepare(waiting(), {"combat": attempt()}, native_snapshot)
        assert prepared.snapshot is not None
        prepared.prepare(waiting(), {"combat": attempt()}, forbidden)


def test_entry_refresh_retains_only_previous_character_evidence_across_pending_read():
    prepared = NativeBattlePreparation()
    old = native_snapshot()
    prepared.prepare(waiting(), {}, lambda: old)
    prepared.prepare(waiting("2"), {}, lambda: {"state": "source_changed"})
    new = native_snapshot()
    new["domains"]["character"]["revision"] = "2"
    with patch("src.services.native_battle_preparation.monotonic", return_value=10**10):
        result = prepared.prepare(waiting("2"), {}, lambda: new)
    assert result["prior_character_observation"] == old["domains"]["character"]
    old["domains"]["character"]["revision"] = "changed"
    assert result["prior_character_observation"]["revision"] == "1"


def test_backpack_refresh_does_not_reprepare_a_frozen_role_panel():
    prepared = NativeBattlePreparation()
    record = waiting()
    with patch("src.services.native_battle_preparation.monotonic", return_value=0):
        prepared.prepare(record, {}, native_snapshot)
        changed = deepcopy(record)
        changed["native_capture"]["contextEvents"][0]["snapshotChanges"]["inventory"]["revision"] = "999"
        prepared.prepare(changed, {}, lambda: (_ for _ in ()).throw(AssertionError("backpack triggered panel read")))


def test_direct_hit_reference_beats_stale_context_but_requires_same_provider():
    snapshot = native_snapshot()
    evidence = attempt(revision="0")
    evidence["firstSnapshotRefs"] = {}
    for domain, row in snapshot["domains"].items():
        row["domain"] = domain
        evidence["firstSnapshotRefs"][domain] = {k: row[k] for k in ("providerId", "domain", "domainKey", "revision")}
    assert validate_first_hit(snapshot, evidence) is None
    evidence["firstSnapshotRefs"]["character"]["providerId"] = "other-provider"
    assert validate_first_hit(snapshot, evidence) == "first_hit_configuration_unverified"


def test_lease_uses_prepared_snapshot_even_if_first_hit_is_only_seen_at_stop():
    from src.services.native_game_session import NativeGameSession
    from tests.test_native_game_session import FakeNativeCore
    session = NativeGameSession(FakeNativeCore, lambda _cap: None)
    lease = session.battle_client()
    try:
        with patch("src.integrations.native_battle_snapshot.freeze_native_battle_snapshot", return_value=native_snapshot()) as read:
            lease.start_capture(profile="combat")
            record = waiting()
            lease.observe_battle_scopes(record)
            assert read.call_count == 1
            record = deepcopy(record)
            record["native_capture"]["scopeAttempts"] = {"combat": attempt()}
            result = lease.observe_battle_scopes(record, final=True)
            assert result["scopes"]["combat"]["snapshot"]["state"] == "observed"
            assert read.call_count == 1
    finally:
        lease.close()
        session.close()


def test_snapshot_rpc_rejection_keeps_safe_diagnostic_and_capture_alive():
    from src.integrations.nte_core_protocol import NteCoreRpcError
    from src.services.native_game_session import NativeGameSession
    from tests.test_native_game_session import FakeNativeCore
    for code, expected in [("NATIVE_SNAPSHOT_NOT_FOUND", "NATIVE_SNAPSHOT_NOT_FOUND"),
                           ("private-provider-text", "unrecognized_rpc_error")]:
        session = NativeGameSession(FakeNativeCore, lambda _cap: None)
        lease = session.battle_client()
        error = NteCoreRpcError({"code": -32000, "message": "private-provider-text",
                                 "data": {"domain_code": code}})
        try:
            lease.start_capture(profile="combat")
            record = waiting()
            record["native_capture"]["scopeAttempts"] = {"combat": attempt()}
            with patch("src.integrations.native_battle_snapshot.freeze_native_battle_snapshot", side_effect=error):
                result = lease.observe_battle_scopes(record)
            frozen = result["scopes"]["combat"]["snapshot"]
            assert frozen["state"] == "unavailable"
            assert frozen["diagnostic"] == {"error_type": "NteCoreRpcError", "rpc_code": -32000,
                                            "domain_code": expected}
            assert "private-provider-text" not in str(frozen)
        finally:
            lease.close()
            session.close()


def test_first_hit_waits_for_pending_baseline_and_freezes_only_once():
    from src.services.native_game_session import NativeGameSession
    from tests.test_native_game_session import FakeNativeCore
    session = NativeGameSession(FakeNativeCore, lambda _cap: None)
    lease = session.battle_client()
    record = waiting()
    record["native_capture"]["scopeAttempts"] = {"combat": attempt()}
    try:
        lease.start_capture(profile="combat")
        with patch("src.services.native_battle_preparation.monotonic", return_value=0), patch(
                "src.integrations.native_battle_snapshot.freeze_native_battle_snapshot",
                return_value={"state": "source_changed", "domains": {}, "missing": ["capture_start_snapshot_not_stable"]}) as read:
            assert lease.observe_battle_scopes(record) is None
            assert lease.observe_battle_scopes(record) is None
            assert read.call_count == 1
        with patch("src.services.native_battle_preparation.monotonic", return_value=3), patch(
                "src.integrations.native_battle_snapshot.freeze_native_battle_snapshot", return_value=native_snapshot()) as read:
            result = lease.observe_battle_scopes(record)
            assert result["scopes"]["combat"]["snapshot"]["state"] == "observed"
            assert lease.observe_battle_scopes(record) is None
            assert read.call_count == 1
    finally:
        lease.close()
        session.close()
