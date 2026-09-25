# 验证战报入场快照的跨域版本、分页完整性、重试和取消边界。
from copy import deepcopy
from unittest.mock import patch

import pytest

from src.integrations.native_battle_snapshot import DOMAINS, freeze_native_battle_snapshot
from src.integrations.nte_core_protocol import NteCoreProtocolError, NteCoreRpcError


class Core:
    def __init__(self):
        self.hello_result = {"capabilities": ["snapshot.changes.v1", *[f"{d}.snapshot.v1" for d in DOMAINS]]}
        self.revision = "1"
        self.calls = []
        self.on_status = None
        self.on_page = None

    def header(self, domain):
        return dict(providerId="p", domain=domain, snapshotId=domain + self.revision,
                    generation="1", domainKey="scene/" + domain, revision=self.revision,
                    observedUnixUs="1000", observedMonotonicMs="1", ready=True, enabled=True,
                    dirty=False, recordCount=1, enumerationComplete=True, complete=False,
                    sourceCoverage="unknown", changeCoverage="unknown", failed=False, truncated=False, missing=[])

    def call(self, method, params, **kwargs):
        self.calls.append((method, deepcopy(params)))
        if method == "native.snapshot.status":
            if self.on_status:
                self.on_status(self)
            return {"providerId": "p", "domains": [self.header(d) for d in DOMAINS]}
        result = self.header(params["domain"])
        if method == "native.snapshot.page":
            result.update(records=[{"source": "fixture", "bIsTemporary": True}], nextOffset=None)
            if self.on_page:
                self.on_page(result)
        return result


def test_entry_refresh_retains_prior_character_evidence_from_shared_baseline():
    from src.integrations.native_snapshot_baseline import NativeSnapshotBaseline
    core = Core()
    baseline = NativeSnapshotBaseline()
    previous = {**core.header("character"), "records": [{"ItemID": "1072"}]}
    baseline.put("character", previous)
    core.revision = "2"
    result = freeze_native_battle_snapshot(core, lambda: None, baseline=baseline)
    assert result["domains"]["character"]["revision"] == "2"
    assert result["prior_character_observation"] == previous
    previous["records"].clear()
    assert result["prior_character_observation"]["records"] == [{"ItemID": "1072"}]


def test_prior_character_evidence_uses_same_team_subset_as_current_observation():
    from src.services.native_battle_team_snapshot import select_native_team_snapshot
    snapshot = {"domains": {"character": {"records": [{"ItemID": "1072"}, {"ItemID": "1004"}]},
        "team": {"records": [{"CharacterItems": [{"ItemID": "1072"}]}]}},
        "prior_character_observation": {"revision": "1", "recordCount": 2,
            "records": [{"ItemID": "1072"}, {"ItemID": "1004"}]}}
    selected = select_native_team_snapshot(snapshot)
    prior = selected["prior_character_observation"]
    assert prior["records"] == selected["domains"]["character"]["records"] == [{"ItemID": "1072"}]
    assert prior["recordCount"] == 2
    assert len(snapshot["prior_character_observation"]["records"]) == 2


def test_freezes_trial_raw_without_promoting_unknown_or_mutating_source():
    core = Core()
    result = freeze_native_battle_snapshot(core, lambda: None)
    assert result["state"] == "observed"
    assert set(result["domains"]) == set(DOMAINS)
    assert result["domains"]["character"]["records"][0]["bIsTemporary"] is True
    assert result["domains"]["character"]["complete"] is False
    assert "inventory_projection_unavailable" in result["missing"]
    core.revision = "2"
    assert result["domains"]["character"]["revision"] == "1"


def test_formal_and_raw_pages_share_one_refresh_per_domain():
    core = Core()
    core.hello_result["capabilities"].extend(["native_inventory_dto_v1", "native_character_profile_v1"])
    headers = {}
    def project(_call, _check, *, domain, header):
        headers[domain] = deepcopy(header)
        return deepcopy(header)
    with patch("src.integrations.native_battle_snapshot.read_native_projection", side_effect=project):
        result = freeze_native_battle_snapshot(core, lambda: None)
    assert result["state"] == "observed"
    assert [p["domain"] for m, p in core.calls if m == "native.snapshot.refresh"] == list(DOMAINS)
    for domain in ("character", "inventory"):
        assert headers[domain]["snapshotId"] == result["domains"][domain]["snapshotId"]


def test_scene_change_after_read_keeps_immutable_observations_for_first_hit_validation():
    core = Core()
    def changed(c):
        c.revision = "2"
        c.on_status = None
    core.on_status = changed
    result = freeze_native_battle_snapshot(core, lambda: None)
    assert result["state"] == "observed"
    assert {s["revision"] for s in result["domains"].values()} == {"1"}
    assert sum(m == "native.snapshot.refresh" for m, _ in core.calls) == 4


def test_live_revision_changes_do_not_discard_completed_pages():
    core = Core()
    core.on_status = lambda c: setattr(c, "revision", str(int(c.revision) + 1))
    result = freeze_native_battle_snapshot(core, lambda: None)
    assert result["state"] == "observed"
    assert {s["revision"] for s in result["domains"].values()} == {"1"}


def test_incomplete_page_never_becomes_battle_snapshot():
    core = Core()
    core.on_page = lambda page: page.update(records=[])
    with pytest.raises(NteCoreProtocolError):
        freeze_native_battle_snapshot(core, lambda: None)


def test_cancellation_stops_before_other_domains():
    core = Core()
    def check():
        if len(core.calls) >= 2:
            raise PermissionError("cancelled")
    with pytest.raises(PermissionError):
        freeze_native_battle_snapshot(core, check)
    assert len(core.calls) == 2


def test_old_provider_explicitly_reports_missing_without_new_rpc():
    core = Core()
    core.hello_result["capabilities"].remove("snapshot.changes.v1")
    result = freeze_native_battle_snapshot(core, lambda: None)
    assert result["state"] == "unavailable" and core.calls == []


def test_unavailable_domain_retains_other_stable_observations():
    core = Core()
    call = core.call
    def unavailable(method, params, **kwargs):
        if method == "native.snapshot.refresh" and params["domain"] == "character":
            raise NteCoreRpcError({"code": -32001, "message": "not_ready"})
        return call(method, params, **kwargs)
    core.call = unavailable
    result = freeze_native_battle_snapshot(core, lambda: None)
    assert result["state"] == "observed"
    assert set(result["domains"]) == {"inventory", "team", "environment"}
    assert "character_snapshot_unavailable" in result["missing"]


def test_environment_transition_does_not_discard_completed_role_and_equipment():
    core = Core()
    call = core.call
    def transition(method, params, **kwargs):
        if method == "native.snapshot.refresh" and params["domain"] == "environment":
            raise NteCoreRpcError({"code": -32001, "message": "source_changed"})
        return call(method, params, **kwargs)
    core.call = transition
    result = freeze_native_battle_snapshot(core, lambda: None)
    assert result["state"] == "observed"
    assert set(result["domains"]) == {"character", "inventory", "team"}
    assert "environment_snapshot_unavailable" in result["missing"]
    assert sum(m == "native.snapshot.refresh" for m, _ in core.calls) == 3


def test_transient_start_not_ready_preserves_native_lease_for_retry():
    from src.services.native_game_session import NativeGameSession
    from tests.test_native_game_session import FakeNativeCore
    core = FakeNativeCore()
    starts = []
    def start(**kwargs):
        starts.append(kwargs)
        if len(starts) == 1:
            raise NteCoreRpcError({"code": -32001, "message": "not_ready"})
        return {"capture_status": "running"}
    core.start_capture = start
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.battle_client()
    core.hello_result.setdefault("capabilities", []).append("native_battle_scope_snapshot_v1")
    try:
        with pytest.raises(NteCoreRpcError):
            lease.start_capture(profile="combat")
        assert not core.aborted
        assert lease.start_capture(profile="combat")["capture_status"] == "running"
    finally:
        lease.close()
        session.close()
