# 验证先启动计算器再登录游戏时，原生空会话继续等待并自动恢复同步。
from copy import deepcopy

import pytest

from src.integrations.nte_core_protocol import NteCoreProtocolError
from src.services.native_game_session import NativeGameSession
from src.services.native_snapshot_changes import NativeSnapshotChanges, snapshot_change_key
from tests.test_native_change_sync import ChangeCore, status


def test_not_ready_null_domain_key_is_a_valid_waiting_state():
    value = status(dirty=True)
    value["domains"][0]["domainKey"] = None
    assert snapshot_change_key(value, "inventory") == ("p", "", "1")
    value["domains"][0]["ready"] = True
    with pytest.raises(NteCoreProtocolError):
        snapshot_change_key(value, "inventory")


@pytest.mark.parametrize("key", [1, [], {}, "x" * 1025])
def test_not_ready_does_not_hide_malformed_identity(key):
    value = status(dirty=True)
    value["domains"][0]["domainKey"] = key
    with pytest.raises(NteCoreProtocolError):
        snapshot_change_key(value, "inventory")


def test_calc_before_login_waits_then_syncs_and_recovers_after_return_to_login():
    class LoginCore(ChangeCore):
        logged_in = False

        def call(self, method, params, **kwargs):
            if not self.logged_in and method == "native.snapshot.status":
                return {"providerId": "fixture", "domains": [dict(deepcopy(self.header),
                    ready=False, dirty=True, domainKey=None, snapshotId=None, state="not_ready")]}
            if not self.logged_in and method == "native.snapshot.refresh":
                self.calls.append((method, params))
                return dict(deepcopy(self.header), ready=False, dirty=True, domainKey="", state="not_ready")
            return super().call(method, params, **kwargs)

    core, now, events = LoginCore(), [0.0], []
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    lease._changes = NativeSnapshotChanges(clock=lambda: now[0])
    lease.add_event_handler("event.inventory.snapshot", events.append)
    lease.start_capture(profile="inventory")
    try:
        pending = lease.status()
        assert pending["capture_status"] == "running" and not pending["native_snapshot_ready"]
        assert events == []
        core.logged_in, now[0] = True, 1.0
        lease.status()
        now[0] = 1.5
        assert lease.status()["native_snapshot_ready"]
        assert len(events) == 1
        core.logged_in, now[0] = False, 2.0
        assert not lease.status()["native_snapshot_ready"]
        assert len(events) == 1
        # Even an unchanged provider/revision must refresh after the domain disappeared.
        core.logged_in, now[0] = True, 3.0
        lease.status()
        now[0] = 3.5
        assert lease.status()["native_snapshot_ready"]
        assert len(events) == 2
    finally:
        lease.close()
        session.close()
