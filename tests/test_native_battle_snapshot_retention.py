# 验证实时快照停止收尾、环境缺失及离场后保存不丢失已冻结配置。
from copy import deepcopy
from unittest.mock import patch

import pytest

from src.services.native_game_session import NativeGameSession
from tests.test_battle_capture_build_freeze import native_snapshot
from tests.test_native_battle_preparation import waiting
from tests.test_native_battle_scopes import attempt
from tests.test_native_game_session import FakeNativeCore


@pytest.fixture
def capture_lease():
    session = NativeGameSession(FakeNativeCore, lambda _: None)
    lease = session.battle_client()
    lease.start_capture(profile="combat")
    try:
        yield session, lease
    finally:
        lease.close()
        session.close()


def test_stop_before_read_does_not_start_snapshot(capture_lease):
    _, lease = capture_lease
    with patch("src.integrations.native_battle_snapshot.freeze_native_battle_snapshot") as read:
        result = lease._read_battle_snapshot(lambda: True)
    read.assert_not_called()
    assert result["missing"] == ["snapshot_read_cancelled_by_stop"]


@pytest.mark.parametrize("expired", [False, True])
def test_stop_during_read_has_bounded_grace(capture_lease, expired):
    _, lease = capture_lease
    stopped = False
    now = 100.0
    def read(_client, check, **_kwargs):
        nonlocal stopped, now
        check()
        stopped = True
        check()
        now += 5.0 if expired else 4.9
        check()
        return native_snapshot()
    with patch("src.integrations.native_battle_snapshot.freeze_native_battle_snapshot", side_effect=read), patch(
            "src.services.native_game_session.monotonic", side_effect=lambda: now):
        result = lease._read_battle_snapshot(lambda: stopped)
    assert result["state"] == ("unavailable" if expired else "observed")


def test_permission_revocation_during_stop_is_immediate(capture_lease):
    session, lease = capture_lease
    stopped = False
    def revoked(_cap):
        raise PermissionError("revoked")
    def read(_client, check, **_kwargs):
        nonlocal stopped
        stopped = True
        session._guard = revoked
        check()
    with patch("src.integrations.native_battle_snapshot.freeze_native_battle_snapshot", side_effect=read):
        with pytest.raises(PermissionError):
            lease._read_battle_snapshot(lambda: stopped)


def test_environment_missing_does_not_block_role_snapshot(capture_lease):
    _, lease = capture_lease
    snapshot = native_snapshot()
    del snapshot["domains"]["environment"]
    snapshot["missing"].append("environment_snapshot_unavailable")
    record = waiting()
    record["native_capture"]["scopeAttempts"] = {"combat": attempt()}
    with patch("src.integrations.native_battle_snapshot.freeze_native_battle_snapshot", return_value=snapshot):
        result = lease.observe_battle_scopes(record)
    frozen = result["scopes"]["combat"]["snapshot"]
    assert frozen["binding"] == "first_hit_revision"
    assert frozen["missing"] == ["environment_snapshot_unavailable"]


def test_exit_does_not_reread_frozen_upper_but_new_lower_reads(capture_lease):
    _, lease = capture_lease
    record = waiting()
    record["native_capture"]["scopeAttempts"] = {"upper": attempt()}
    with patch("src.integrations.native_battle_snapshot.freeze_native_battle_snapshot", return_value=native_snapshot()) as read:
        result = lease.observe_battle_scopes(record)
        upper = deepcopy(result["scopes"]["upper"])
        record["native_capture"]["contextEvents"] = waiting("2")["native_capture"]["contextEvents"]
        assert lease.observe_battle_scopes(record) is None
        assert read.call_count == 1
        record["native_capture"]["scopeAttempts"]["lower"] = attempt("2")
        result = lease.observe_battle_scopes(record)
        assert read.call_count == 2
        assert result["scopes"]["upper"] == upper
        assert result["scopes"]["lower"]["snapshot"]["binding"] == "first_hit_revision"
        assert lease.observe_battle_scopes(record, final=True) is None
        assert read.call_count == 2


def test_lower_read_never_replaces_upper_pending_configuration(capture_lease):
    from tests.test_native_battle_team_continuity import observed_team

    _, lease = capture_lease
    upper, evidence, record = observed_team()
    # First-hit character evidence is unresolved, but this is the observed upper roster.
    upper["domains"]["character"]["revision"] = "2"
    record["native_capture"]["scopeAttempts"] = {"upper": evidence}
    with patch("src.integrations.native_battle_snapshot.freeze_native_battle_snapshot", return_value=upper):
        assert lease.observe_battle_scopes(record) is None
    lower = native_snapshot()
    for row in lower["domains"].values():
        row["revision"] = "3"
    lower["domains"]["team"]["records"] = [{"CharacterItems": [{"ItemID": "1004"}]}]
    later = deepcopy(record["native_capture"]["contextEvents"][-1])
    later["observedUnixUs"] = "4000000"
    later["snapshotChanges"] = {d: {"revision": "3", "dirty": False} for d in lower["domains"]}
    record["native_capture"]["contextEvents"].append(later)
    record["native_capture"]["scopeAttempts"]["lower"] = attempt("2", "3")
    with patch("src.integrations.native_battle_snapshot.freeze_native_battle_snapshot", return_value=lower):
        live = lease.observe_battle_scopes(record)
    assert set(live["scopes"]) == {"lower"}
    final = lease.observe_battle_scopes(record, final=True)
    saved_upper = final["scopes"]["upper"]["snapshot"]
    assert saved_upper["binding"] == "pending_first_hit"
    assert saved_upper["domains"]["character"]["revision"] == "2"
    assert saved_upper["domains"]["team"]["records"] == upper["domains"]["team"]["records"]


def test_late_context_cannot_discard_first_hit_matching_pending_snapshot():
    from src.services.native_battle_preparation import retain_scope_pending_snapshot
    from src.services.native_battle_scopes import validate_first_hit
    from tests.test_native_battle_team_continuity import observed_team

    upper, attempt, record = observed_team()
    late_record = deepcopy(record)
    late_record["native_capture"]["contextEvents"].pop()
    assert validate_first_hit(upper, attempt, late_record) is not None
    lower = deepcopy(upper)
    lower["domains"]["character"]["revision"] = "3"
    retained = retain_scope_pending_snapshot(upper, lower, attempt, late_record)
    assert retained == upper
    # Retention is not admission: the matching context is still required.
    assert validate_first_hit(retained, attempt, late_record) is not None
    assert validate_first_hit(retained, attempt, record) is None


@pytest.mark.parametrize("changed_domain", ["character", "inventory", "team"])
def test_pending_first_hit_domain_cannot_be_replaced_during_context_lag(changed_domain):
    from src.services.native_battle_preparation import retain_scope_pending_snapshot
    previous = native_snapshot()
    candidate = deepcopy(previous)
    candidate["domains"][changed_domain]["revision"] = "2"
    record = {"native_capture": {"providerId": "fixture"}}
    assert retain_scope_pending_snapshot(previous, candidate, attempt(), record) == previous


def test_context_arriving_after_lower_read_still_recovers_upper(capture_lease):
    from tests.test_native_battle_team_continuity import observed_team
    _, lease = capture_lease
    upper, evidence, complete = observed_team()
    complete["native_capture"]["scopeAttempts"] = {"upper": evidence}
    delayed = deepcopy(complete)
    delayed["native_capture"]["contextEvents"].pop()
    with patch("src.integrations.native_battle_snapshot.freeze_native_battle_snapshot", return_value=upper):
        assert lease.observe_battle_scopes(delayed) is None
    lower = native_snapshot()
    for row in lower["domains"].values():
        row["revision"] = "3"
    delayed["native_capture"]["scopeAttempts"]["lower"] = attempt("2", "3")
    context = deepcopy(delayed["native_capture"]["contextEvents"][-1])
    context["observedUnixUs"] = "4000000"
    context["snapshotChanges"] = {key: {"revision": "3", "dirty": False} for key in lower["domains"]}
    delayed["native_capture"]["contextEvents"].append(context)
    with patch("src.integrations.native_battle_snapshot.freeze_native_battle_snapshot", return_value=lower):
        live = lease.observe_battle_scopes(delayed)
    assert set(live["scopes"]) == {"lower"}
    complete["native_capture"]["scopeAttempts"]["lower"] = attempt("2", "3")
    complete["native_capture"]["contextEvents"].append(context)
    final = lease.observe_battle_scopes(complete, final=True)
    assert final["scopes"]["upper"]["snapshot"]["domains"] == upper["domains"]
    assert final["scopes"]["upper"]["snapshot"]["binding"] == "first_hit_revision"
    assert final["scopes"]["lower"] == live["scopes"]["lower"]
