# 验证原生装备能力、本次完整背包门禁及原始命令回执，不依赖旧装备管道。
import json
from unittest.mock import Mock

import pytest

from src.integrations.nte_core_protocol import NteCoreRpcError
from src.services.native_game_session import NativeGameSession
from src.services.work_mode_checks import build_work_mode_report
from tests.test_native_inventory_sync_integration import ProjectionCore
from tests.test_native_plugin_runtime import setup_runtime, module


class EquipmentCore(ProjectionCore):
    def __init__(self):
        super().__init__(count=1)
        self.hello_result["capabilities"].extend(("equipment", "native_equipment_v1"))
        self.ready = True
        self.equip_core = Mock(return_value={"status": "rpc_dispatched"})
        self.move_core_to_character = Mock(return_value={"status": "rpc_dispatched"})

    def call(self, method, params, **kwargs):
        if method == "equipment.status":
            self.calls.append((method, params))
            return {"ready": self.ready, "providerId": "fixture", "epoch": "8",
                    "domainKey": "equipment-context-distinct-from-inventory"}
        return super().call(method, params, **kwargs)


def test_background_observation_keeps_sync_without_dispatching_equipment_checks():
    core = EquipmentCore()
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    lease.start_capture(profile="inventory")
    try:
        assert lease.status()["native_snapshot_ready"]
        core.calls.clear()
        for _ in range(3):
            report = session.inspect(refresh=True, check_equipment=False)
            assert report["inventory_snapshot_ready"] and report["equipment"] is None
        assert not any(method == "equipment.status" for method, _params in core.calls)
        assert not any(method == "native.snapshot.refresh" for method, _params in core.calls)
        assert any(method == "native.snapshot.status" for method, _params in core.calls)
        assert session.inspect(check_equipment=True)["equipment"]["ready"]
        core.ready = False
        with pytest.raises(NteCoreRpcError):
            lease.equip_core(character={"slot": 700, "serial": 701}, equipment={"slot": 8, "serial": 1})
        core.equip_core.assert_not_called()
    finally:
        lease.close()
        session.close()


def test_background_runtime_defers_equipment_probe_until_explicit_detection(tmp_path, monkeypatch):
    runtime, policy, _game, _ = setup_runtime(tmp_path, monkeypatch)
    manifest = runtime.root / "component-bundle.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["capabilities"].append("equipment.execute.v1")
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    runtime._game_running = lambda: True
    monkeypatch.setattr(module, "native_capture_game_pid", lambda: 123)
    runtime.native_session.inspect = Mock(return_value={
        "hello": {"capabilities": ["equipment", "native_equipment_v1"]}, "status": {}, "domains": {},
        "equipment": None, "inventory_snapshot_ready": True,
    })
    for _ in range(2):
        probe = runtime.tick()
        assert probe.native_equipment.ready is None
        rows = build_work_mode_report(policy.settings, probe).features
        equipment = next(row for row in rows if row.feature == "native_equipment")
        assert equipment.state.value == "waiting" and "实际装配前" in equipment.detail
    assert all(not call.kwargs["check_equipment"] for call in runtime.native_session.inspect.call_args_list)
    runtime.native_session.inspect.return_value["equipment"] = {"ready": True}
    assert runtime.tick(allow_connect=True).native_equipment.ready is True
    assert runtime.native_session.inspect.call_args.kwargs["check_equipment"] is True


def test_equipment_requires_this_leases_complete_inventory_and_preserves_ack():
    core = EquipmentCore()
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    lease.start_capture(profile="inventory")
    command = {"character": {"slot": 700, "serial": 701}, "equipment": {"slot": 8, "serial": 1}}
    try:
        with pytest.raises(NteCoreRpcError, match="本次完整"):
            lease.equip_core(**command)
        core.equip_core.assert_not_called()
        assert lease.status()["native_snapshot_ready"]
        assert lease.equip_core(**command) == {"status": "rpc_dispatched"}
        assert lease.move_core_to_character(**command) == {"status": "rpc_dispatched"}
        core.equip_core.assert_called_once_with(**command)
        core.move_core_to_character.assert_called_once_with(**command)
        assert not session.inspect()["inventory_snapshot_ready"]
        assert lease.status()["native_snapshot_ready"]
        core.projection_complete = False
        assert not lease.status()["native_snapshot_ready"]
        with pytest.raises(NteCoreRpcError, match="EQUIPMENT_PLUGIN_BUSY"):
            lease.equip_core(**command)
        assert core.equip_core.call_count == 1
    finally:
        lease.close()
        session.close()


@pytest.mark.parametrize("missing", ["ready", "equipment", "native_equipment_v1"])
def test_equipment_rechecks_provider_capabilities_and_ready_before_dispatch(missing):
    core = EquipmentCore()
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    lease.start_capture(profile="inventory")
    try:
        assert lease.status()["native_snapshot_ready"]
        if missing == "ready":
            core.ready = False
        else:
            core.hello_result["capabilities"].remove(missing)
        with pytest.raises(NteCoreRpcError):
            lease.equip_core(character={"slot": 700, "serial": 701}, equipment={"slot": 8, "serial": 1})
        core.equip_core.assert_not_called()
    finally:
        lease.close()
        session.close()


@pytest.mark.parametrize("reason", ["not_ready", "source_changed"])
@pytest.mark.parametrize("active_battle", [False, True])
def test_waiting_equipment_inspection_keeps_shared_owner_and_battle(reason, active_battle):
    core = EquipmentCore()
    original = core.call
    def call(method, params, **kwargs):
        if method == "equipment.status":
            raise NteCoreRpcError({"code": -32001, "message": reason})
        return original(method, params, **kwargs)
    core.call = call
    session = NativeGameSession(lambda: core, lambda _cap: None)
    battle = session.battle_client() if active_battle else None
    try:
        result = session.inspect()
        assert result["equipment"] == {"ready": False, "reason": reason}
        assert not session._failed and session._client is core
        assert not core.closed and not core.aborted
        if battle is not None:
            assert battle.get_battle_record()["final"]
    finally:
        if battle is not None:
            battle.close()
        session.close()


@pytest.mark.parametrize("inventory_ready", [False, True])
def test_native_runtime_uses_shared_status_and_current_inventory_without_legacy_pipe(tmp_path, monkeypatch, inventory_ready):
    runtime, policy, _game, _ = setup_runtime(tmp_path, monkeypatch)
    manifest = runtime.root / "component-bundle.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["capabilities"].append("equipment.execute.v1")
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    runtime._game_running = lambda: True
    monkeypatch.setattr(module, "native_capture_game_pid", lambda: 123)
    legacy = Mock(side_effect=AssertionError("native equipment cannot probe the old pipe"))
    monkeypatch.setattr(module, "probe_equipment_pipe", legacy)
    runtime.native_session.inspect = Mock(return_value={
        "hello": {"capabilities": ["equipment", "native_equipment_v1"]}, "status": {}, "domains": {},
        "equipment": {"ready": True}, "inventory_snapshot_ready": inventory_ready,
    })
    probe = runtime.tick(allow_connect=True)
    assert probe.native_equipment.ready and probe.native_equipment.supported
    report = build_work_mode_report(policy.settings, probe)
    equipment = next(row for row in report.features if row.feature == "native_equipment")
    assert equipment.state.value == ("available" if inventory_ready else "waiting")
    legacy.assert_not_called()
