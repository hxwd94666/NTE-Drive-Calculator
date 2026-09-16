# 串联真实原生会话、分页租约、稳定器和临时库存库，验证共享所有者与取消边界。
from copy import deepcopy
import json
from pathlib import Path
from threading import Event

import pytest

from src.integrations.nte_core_protocol import NteCoreRpcError
from src.services.inventory_sync_service import InventorySyncService
from src.services.native_game_session import NativeGameSession
from src.services.official_role_page_service import load_official_role_detail
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_native_game_session import FakeNativeCore
from tests.test_user_data_inventory_dao import item, snapshot


class ProjectionCore(FakeNativeCore):
    def __init__(self, *, count=65):
        super().__init__()
        self.hello_result["capabilities"].append("native_inventory_dto_v1")
        self.hello_result["protocol_version"] = 1
        self.rows = [item(n + 1, 8) for n in range(count)]
        if self.rows:
            self.rows[0].update(equipped=True, equipped_character_id=1003,
                                equipped_character_uid={"slot": 700, "serial": 701})
        self.characters = [{"character_id": 1003, "uid": {"slot": 700, "serial": 701}}]
        self.header = dict(providerId="fixture", domain="inventory", snapshotId="9", generation="2",
                           domainKey="fixture-domain", observedUnixUs="1800000000000000", observedMonotonicMs="50",
                           enabled=True, ready=True, state="ready", recordCount=count, sourceRecordCount=count,
                           enumerationComplete=True, collectionComplete=True, collectionScope="EQUIP",
                           characterRefsComplete=True, complete=False, sourceCoverage="unknown",
                           changeCoverage="unknown", missing=[], failed=False, truncated=False)
        self.projection_complete = True
        self.on_page = None

    def call(self, method, params, **kwargs):
        self.calls.append((method, deepcopy(params)))
        if method == "native.snapshot.refresh":
            return deepcopy(self.header)
        if method == "native.inventory.page":
            offset = params["offset"]
            end = min(offset + params["limit"], len(self.rows))
            result = dict(deepcopy(self.header), items=deepcopy(self.rows[offset:end]),
                          characters=deepcopy(self.characters), projectionComplete=self.projection_complete,
                          referencedItemUids=[{"slot": 8, "serial": 1}] if self.rows else [],
                          nextOffset=end if end < len(self.rows) else None)
            if self.on_page:
                self.on_page(offset, result)
            return result
        return {"domains": []}


def setup_sync(tmp_path, core, *, guard=lambda _cap: None, context_key=lambda: 1):
    clients = []
    def factory():
        clients.append(core)
        return core
    session = NativeGameSession(factory, guard, context_key)
    service = InventorySyncService(
        tmp_path / "account.sqlite3", account_id="fixture", capture_source="native",
        client_factory=session.inventory_client, operation_guard=guard,
        settle_seconds=0.03, poll_seconds=0.005,
    )
    return session, service, clients


def test_real_native_lease_saves_paginated_dto_and_game_current_projection(tmp_path):
    core = ProjectionCore()
    session, service, clients = setup_sync(tmp_path, core)
    battle = None
    try:
        service.start()
        state = service.wait_for_snapshot(timeout=4)
        battle = session.battle_client()
        battle.start()
        battle.start_capture(profile="combat")
        service.stop()
        assert clients == [core] and not core.closed and session.battle_active
        assert battle.get_battle_record()["final"]
        assert [params["offset"] for method, params in core.calls if method == "native.inventory.page"] == [0, 64]
        assert [params["profile"] for method, params in core.calls if method == "capture.start"] == ["combat"]
        with UserDataDao(service.database_path) as dao:
            assert dao.current_inventory_summary()["stored_item_count"] == 65
            assert len(dao.list_current_inventory_items(equipped=True, character_id=1003)) == 1
            assert dao.list_character_instance_mappings(1003)[0]["uid_serial"] == 701
            raw = dao.raw_snapshot(state.last_snapshot_id)["params"]
            assert raw["complete"] is True and raw["native_snapshot"]["projectionComplete"] is True
            assert raw["native_snapshot"]["complete"] is False
            assert raw["native_snapshot"]["sourceCoverage"] == "unknown"
        detail = load_official_role_detail(service.database_path, 1003,
                                           shared_database_path=tmp_path / "unused-shared.sqlite3")
        current = detail["equipment_contexts"]["current"]
        assert current["available"] and current["title"] == "游戏当前"
        assert [(row["uid_slot"], row["uid_serial"]) for row in current["items"]] == [(8, 1)]
    finally:
        service.stop()
        if battle is not None:
            battle.close()
        session.close()
    assert core.closed


def test_empty_login_observation_waits_then_accepts_loaded_inventory():
    core = ProjectionCore(count=0)
    characters = core.characters
    core.characters = []
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    received = []
    lease.add_event_handler("event.inventory.snapshot", received.append)
    lease.start_capture(profile="inventory")
    try:
        assert not lease.status()["native_snapshot_ready"]
        assert received == []
        # An empty inventory with observed characters is valid; no item-count floor.
        core.characters = characters
        assert lease.status()["native_snapshot_ready"]
        assert received[-1]["params"]["item_count"] == 0
        core.rows = [item(1, 8)]
        core.header.update(recordCount=1, sourceRecordCount=1)
        assert lease.status()["native_snapshot_ready"]
        assert received[-1]["params"]["item_count"] == 1
    finally:
        lease.close()
        session.close()


@pytest.mark.parametrize("active_battle", [False, True])
def test_read_control_timeout_preserves_owner_and_retries_new_complete_inventory(active_battle):
    core = ProjectionCore(count=1)
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    battle = session.battle_client() if active_battle else None
    received = []
    lease.add_event_handler("event.inventory.snapshot", received.append)
    lease.start_capture(profile="inventory")
    original = core.call
    fail = [True]
    def call(method, params, **kwargs):
        if method == "native.snapshot.refresh" and fail[0]:
            raise NteCoreRpcError({"code": -32001, "message": "control_timeout"})
        return original(method, params, **kwargs)
    core.call = call
    try:
        if battle:
            assert not lease.status()["native_snapshot_ready"]
            assert battle.get_battle_record()["final"]
            battle.close()
            battle = None
        pending = lease.status()
        assert pending["capture_status"] == "running" and not pending["native_snapshot_ready"]
        assert "自动重试" in pending["message"] and received == []
        assert not core.closed and not core.aborted and not session._failed
        fail[0] = False
        assert lease.status()["native_snapshot_ready"]
        assert len(received) == 1 and received[0]["params"]["item_count"] == 1
        if battle:
            assert battle.get_battle_record()["final"]
    finally:
        lease.close()
        if battle:
            battle.close()
        session.close()


@pytest.mark.parametrize("reason", ["ready", "enumeration", "projection"])
def test_pending_real_projection_preserves_existing_current_snapshot(tmp_path, reason):
    core = ProjectionCore()
    if reason == "ready": core.header["ready"] = False
    elif reason == "enumeration": core.header["enumerationComplete"] = False
    else: core.projection_complete = False
    session, service, _clients = setup_sync(tmp_path, core)
    with UserDataDao(service.database_path, account_id="fixture") as dao:
        baseline = dao.import_inventory_snapshot(snapshot(1, [item(500, 8)]))
    pending = Event()
    service.add_state_handler(lambda state: pending.set() if state.phase == "waiting" else None)
    try:
        service.start()
        assert pending.wait(3)
        service.stop()
        assert service.state.last_snapshot_id == baseline and not service.state.source_snapshot_ready
        with UserDataDao(service.database_path) as dao:
            assert dao.current_inventory_snapshot_id() == baseline
            assert [row["uid_serial"] for row in dao.list_current_inventory_items()] == [500]
    finally:
        service.stop()
        session.close()


@pytest.mark.parametrize("change", ["permission", "generation", "stop"])
def test_real_paginated_lease_cannot_save_after_revocation(tmp_path, change):
    permitted, generation, reached = [True], [1], Event()
    def guard(_cap):
        if not permitted[0]: raise PermissionError("revoked")
    core = ProjectionCore()
    session, service, _clients = setup_sync(tmp_path, core, guard=guard, context_key=lambda: generation[0])
    def interrupt(offset, _page):
        if offset != 0: return
        if change == "permission": permitted[0] = False
        elif change == "generation": generation[0] += 1
        else: service.request_stop()
        reached.set()
    core.on_page = interrupt
    try:
        service.start()
        assert reached.wait(3)
        service.stop()
        with UserDataDao(service.database_path) as dao:
            assert dao.current_inventory_snapshot_id() is None
        assert not service.state.source_snapshot_ready
        if change in {"generation", "stop"}:
            assert service.state.phase == "stopped"
            assert service.state.error is None and service.state.error_code is None
        assert [params["offset"] for method, params in core.calls if method == "native.inventory.page"] == [0]
    finally:
        service.stop()
        session.close()


def test_real_missing_native_contract_fails_without_packet_or_write(tmp_path):
    core = ProjectionCore()
    core.hello_result["capabilities"].remove("native_inventory_dto_v1")
    session, service, clients = setup_sync(tmp_path, core)
    try:
        service.start()
        state = service.wait_for_phase("error", timeout=3)
        assert state.error_code == "NATIVE_CAPABILITY_MISSING"
        assert state.last_snapshot_id is None and clients == [core]
        assert not any(method in {"capture.start", "native.inventory.page"} for method, _ in core.calls)
    finally:
        service.stop()
        session.close()


def test_inventory_restart_reuses_core_and_deduplicates_same_complete_dto(tmp_path):
    core = ProjectionCore(count=1)
    session, service, clients = setup_sync(tmp_path, core)
    try:
        service.start()
        saved = service.wait_for_snapshot(timeout=3).last_snapshot_id
        service.stop()
        assert not core.closed
        received = Event()
        service.add_state_handler(lambda state: received.set() if state.source_snapshot_ready else None)
        service.start()
        assert received.wait(3)
        service.stop()
        assert clients == [core]
        with UserDataDao(service.database_path) as dao:
            assert dao.current_inventory_snapshot_id() == saved
            assert len(dao.list_inventory_snapshots()) == 1
    finally:
        service.stop()
        session.close()


@pytest.mark.parametrize("failure", ["NATIVE_MAPPING_UNSUPPORTED", "NATIVE_SNAPSHOT_INCOMPLETE", "mixed_refs", "uncovered_refs"])
def test_second_page_rejection_never_advances_existing_snapshot(tmp_path, failure):
    core = ProjectionCore()
    session, service, _clients = setup_sync(tmp_path, core)
    with UserDataDao(service.database_path, account_id="fixture") as dao:
        baseline = dao.import_inventory_snapshot(snapshot(1, [item(500, 8)]))
    def reject(offset, page):
        if failure == "uncovered_refs":
            page["referencedItemUids"] = [{"slot": 8, "serial": 10000}]
        elif offset:
            if failure == "mixed_refs": page["referencedItemUids"] = []
            else: raise NteCoreRpcError({"code": -32001, "message": "synthetic producer rejection",
                                        "data": {"domain_code": failure}})
    core.on_page = reject
    try:
        service.start()
        state = service.wait_for_phase("waiting" if failure == "NATIVE_SNAPSHOT_INCOMPLETE" else "error", timeout=3)
        service.stop()
        if failure == "NATIVE_MAPPING_UNSUPPORTED": assert state.error_code == failure
        if failure in {"NATIVE_MAPPING_UNSUPPORTED", "NATIVE_SNAPSHOT_INCOMPLETE"}:
            assert not core.closed
        assert state.last_snapshot_id == baseline and not state.source_snapshot_ready
        with UserDataDao(service.database_path) as dao:
            assert dao.current_inventory_snapshot_id() == baseline
            assert len(dao.list_inventory_snapshots()) == 1
    finally:
        service.stop()
        session.close()


def test_public_producer_fixture_reaches_real_dao_without_field_reinterpretation(tmp_path):
    fixture = json.loads((Path(__file__).parent / "fixtures/native_business_222.json").read_text(encoding="utf-8"))
    assert fixture["synthetic"] is True
    inventory = fixture["inventory"]
    for envelope in ("refresh", "response"):
        proof = inventory[envelope]["result"]
        assert proof["collectionComplete"] is True
        assert proof["collectionScope"] == "EQUIP"
        assert proof["characterRefsComplete"] is True
    class SerializerCore(ProjectionCore):
        def call(self, method, params, **kwargs):
            self.calls.append((method, deepcopy(params)))
            if method == "native.snapshot.refresh":
                assert params == {"domain": "inventory"}
                return deepcopy(inventory["refresh"]["result"])
            if method == "native.inventory.page":
                assert params == inventory["request"]["params"]
                return deepcopy(inventory["response"]["result"])
            return {}
    core = SerializerCore(count=1)
    session, service, clients = setup_sync(tmp_path, core)
    try:
        service.start()
        state = service.wait_for_snapshot(timeout=3)
        service.stop()
        with UserDataDao(service.database_path) as dao:
            stored = dao.raw_snapshot(state.last_snapshot_id)["params"]
            assert stored["items"] == inventory["response"]["result"]["items"]
            assert stored["characters"] == inventory["response"]["result"]["characters"]
            assert stored["native_snapshot"]["statProvenance"] == inventory["response"]["result"]["statProvenance"]
            current = dao.list_current_inventory_items(equipped=True, character_id=1020)
            assert len(current) == 1 and (current[0]["uid_slot"], current[0]["uid_serial"]) == (301, 401)
            assert clients == [core]
    finally:
        service.stop()
        session.close()


@pytest.mark.parametrize("active_battle", [False, True])
def test_projection_protocol_fault_reconnects_after_required_battle_drain(tmp_path, active_battle):
    broken, replacement = ProjectionCore(), ProjectionCore(count=1)
    battle = None
    def corrupt(offset, page):
        nonlocal battle
        if not offset and active_battle:
            battle = session.battle_client()
            battle.start_capture(profile="combat")
        if offset: page["snapshotId"] = "other-snapshot"
    broken.on_page = corrupt
    created = []
    def factory():
        core = (broken, replacement)[len(created)]
        created.append(core)
        return core
    session = NativeGameSession(factory, lambda _cap: None)
    service = InventorySyncService(
        tmp_path / "protocol.sqlite3", account_id="fixture", capture_source="native",
        client_factory=session.inventory_client, operation_guard=lambda _cap: None,
        settle_seconds=0.03, poll_seconds=0.005,
    )
    try:
        service.start()
        state = service.wait_for_phase("error", timeout=3)
        service.stop()
        assert state.error_code == "NteCoreProtocolError"
        assert state.last_snapshot_id is None
        assert broken.closed is (not active_battle)
        if battle:
            assert battle.get_battle_record()["final"]
            with pytest.raises(RuntimeError):
                session.inventory_client()
            assert created == [broken] and not broken.closed
            battle.close()
            assert broken.closed
        service.start()
        recovered = service.wait_for_snapshot(timeout=3)
        assert recovered.source_snapshot_ready
        assert created == [broken, replacement]
    finally:
        service.stop()
        if battle: battle.close()
        session.close()
