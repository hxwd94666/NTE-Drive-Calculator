# 验证自动同步与战报复用同一基线，变化仅刷新所属域，半场副本互不覆盖。
from collections import Counter
from copy import deepcopy

import pytest

from src.integrations.native_inventory_snapshot import NativeSnapshotPending
from src.services.native_session.session import NativeGameSession
from tests.test_native_game_session import FakeNativeCore
from tests.test_native_battle_scopes import attempt


class BaselineCore(FakeNativeCore):
    def __init__(self):
        super().__init__()
        self.hello_result["capabilities"] += ["snapshot.changes.v1", "native_inventory_dto_v1", "native_character_profile_v1"]
        self.revisions = dict.fromkeys(("character", "inventory", "team", "environment"), "1")
        self.provider = "fixture"
        self.frozen = {}
        self.cursors = {}

    def header(self, domain):
        return dict(providerId=self.provider, domain=domain, snapshotId=domain + self.revisions[domain],
                    generation="1", domainKey="world-1:1/controller-2:2/ps-3:3/" + domain,
                    revision=self.revisions[domain], dirty=False, enabled=True, ready=True, state="ready",
                    observedUnixUs="1000000", observedMonotonicMs="1000", recordCount=1, sourceRecordCount=1,
                    enumerationComplete=True, complete=False, sourceCoverage="unknown", changeCoverage="unknown",
                    missing=[], failed=False, truncated=False, collectionComplete=True,
                    collectionScope="EQUIP", characterRefsComplete=True)

    def call(self, method, params=None, **kwargs):
        self.calls.append((method, deepcopy(params)))
        if method == "core.status":
            return self.status()
        if method == "native.snapshot.status":
            return {"providerId": self.provider, "domains": [self.header(d) for d in self.revisions]}
        if method == "native.snapshot.refresh":
            domain = params["domain"]
            self.frozen[domain] = self.header(domain)
            self.cursors[domain] = [0, 0]
            return deepcopy(self.frozen[domain])
        if method.endswith(".page"):
            raw = method == "native.snapshot.page"
            domain = params["domain"] if raw else method.split(".")[1]
            assert params["snapshotId"] == self.frozen[domain]["snapshotId"]
            assert self.cursors[domain][not raw] == params["offset"]
            self.cursors[domain][not raw] = None
            row = deepcopy(self.frozen[domain])
            row["nextOffset"] = None
            if raw:
                row["records"] = [{"ItemID": "1072", "bIsTemporary": False, "bUnSaved": False}]
                if domain == "team":
                    row["records"] = [{"CharacterItems": [{"ItemID": "1072"}]}]
            elif domain == "inventory":
                row.update(items=[{"uid": {"slot": 1, "serial": 2}, "equipped_character_id": 1072}],
                           characters=[{"character_id": 1072}], referencedItemUids=[], projectionComplete=True)
            else:
                row["profiles"] = [{"character_id": 1072, "character_level": 70, "breakthrough_stage": 5}]
                # 当前角色投影携带在装子集，归属同一 character 修订，不依赖整背包刷新。
                row["battleEquipment"] = {
                    "schemaVersion": 1, "scope": "character_equipped_only", "complete": True,
                    "characters": [{"character_id": 1072}],
                    "items": [{"uid": {"slot": 1, "serial": 2}, "equipped_character_id": 1072}],
                }
            return row
        return {"domains": []}

    def refresh_counts(self):
        return Counter(params["domain"] for method, params in self.calls if method == "native.snapshot.refresh")


def test_sync_first_hit_and_lower_half_share_current_version_without_full_reread():
    core = BaselineCore()
    session = NativeGameSession(lambda: core, lambda _: None)
    inventory = session.inventory_client()
    battle = None
    try:
        inventory.start_capture(profile="inventory")
        assert inventory.status()["native_snapshot_ready"]
        assert core.refresh_counts() == {"inventory": 1}
        assert inventory.status()["native_snapshot_ready"]
        assert core.refresh_counts() == {"inventory": 1, "character": 1}
        battle = session.battle_client()
        battle.start_capture(profile="combat")
        record = {"native_capture": {"scopeAttempts": {"upper": attempt()}}}
        first = battle.observe_battle_scopes(record)
        assert core.refresh_counts() == dict.fromkeys(core.revisions, 1)
        assert first["scopes"]["upper"]["snapshot"]["character_projection"]["revision"] == "1"
        assert "inventory" not in first["scopes"]["upper"]["snapshot"]["domains"]
        assert inventory.status()["native_snapshot_ready"]
        assert core.refresh_counts() == dict.fromkeys(core.revisions, 1)
        # A new half refreshes changed data only; it cannot mutate the upper half.
        core.revisions["character"] = "2"
        record["native_capture"]["contextEvents"] = [{"kind": "combat_context",
            "roots": {"world": "1:1", "controller": "2:2", "ps": "3:3"},
            "snapshotChanges": {domain: {"revision": revision, "dirty": False}
                                for domain, revision in core.revisions.items()}}]
        assert battle.observe_battle_scopes(record) is None
        # 战报所有者仍占用会话，账号读取只消费已确认缓存，不抢占新修订刷新权限。
        with pytest.raises(NativeSnapshotPending):
            session.read_character_profiles()
        assert core.refresh_counts()["character"] == 1
        lower = attempt("2")
        lower["firstChanges"]["character"]["revision"] = "2"
        record["native_capture"]["scopeAttempts"]["lower"] = lower
        both = battle.observe_battle_scopes(record)
        assert core.refresh_counts() == {"inventory": 1, "character": 2, "team": 1, "environment": 1}
        assert both["scopes"]["lower"]["snapshot"]["character_projection"]["revision"] == "2"
        assert both["scopes"]["upper"] == first["scopes"]["upper"]
        # The account reader can consume the version maintained by the battle owner.
        assert session.read_character_profiles()["revision"] == "2"
        assert core.refresh_counts()["character"] == 2
    finally:
        if battle:
            battle.close()
        inventory.close()
        session.close()


def test_new_provider_never_reuses_old_baseline():
    core = BaselineCore()
    session = NativeGameSession(lambda: core, lambda _: None)
    try:
        assert session.read_character_profiles()["providerId"] == "fixture"
        core.provider = "next-provider"
        assert session.read_character_profiles()["providerId"] == "next-provider"
        assert core.refresh_counts()["character"] == 2
    finally:
        session.close()


def test_current_equipped_subset_freezes_without_inventory_domain():
    core = BaselineCore()
    session = NativeGameSession(lambda: core, lambda _: None)
    lease = session.battle_client()
    try:
        lease.start_capture(profile="combat")
        observed = lease.observe_battle_scopes({"native_capture": {
            "scopeAttempts": {"combat": attempt()}}})
        snapshot = observed["scopes"]["combat"]["snapshot"]
        assert snapshot["binding"] == "first_hit_revision"
        assert set(snapshot["domains"]) == {"character", "team", "environment"}
        assert snapshot["character_projection"]["battleEquipment"]["items"][0]["uid"]["serial"] == 2
        assert not any(method == "native.inventory.page" or (
            method == "native.snapshot.refresh" and params["domain"] == "inventory"
        ) for method, params in core.calls)
    finally:
        lease.close()
        session.close()


def test_incomplete_equipped_subset_is_not_published_as_first_hit():
    core = BaselineCore()
    original_call = core.call

    def call(method, params=None, **kwargs):
        result = original_call(method, params, **kwargs)
        if method == "native.character.page":
            result["battleEquipment"]["complete"] = False
        return result

    core.call = call
    session = NativeGameSession(lambda: core, lambda _: None)
    lease = session.battle_client()
    try:
        lease.start_capture(profile="combat")
        record = {"native_capture": {"scopeAttempts": {"combat": attempt()}}}
        assert lease.observe_battle_scopes(record) is None
        final = lease.observe_battle_scopes(record, final=True)
        assert final["scopes"]["combat"]["snapshot"]["state"] == "unavailable"
        assert "inventory" not in core.refresh_counts()
    finally:
        lease.close()
        session.close()


def test_component_inspection_seeds_baseline_instead_of_triggering_duplicate_full_reads():
    core = BaselineCore()
    session = NativeGameSession(lambda: core, lambda _: None)
    inventory = None
    try:
        session.inspect(refresh=True)
        session.inspect(refresh=True)
        inventory = session.inventory_client()
        inventory.start_capture(profile="inventory")
        assert inventory.status()["native_snapshot_ready"]
        assert core.refresh_counts() == dict.fromkeys(core.revisions, 1)
    finally:
        if inventory:
            inventory.close()
        session.close()
