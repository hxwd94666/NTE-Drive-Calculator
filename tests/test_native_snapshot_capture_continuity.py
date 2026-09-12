# 验证首击配置失败只损失配置证据，录制租约仍有效且新半场可重新观察。
from unittest.mock import patch

import pytest

from src.integrations.nte_core_protocol import (
    NteCoreProcessError, NteCoreProtocolError, NteCoreRpcError, NteCoreTimeoutError,
)
from src.services.native_game_session import NativeGameSession
from tests.test_native_game_session import FakeNativeCore
from tests.test_native_battle_scopes import attempt
from tests.test_battle_capture_build_freeze import native_snapshot


@pytest.mark.parametrize("error", [
    NteCoreRpcError({"code": -32001, "message": "capture_busy"}),
    NteCoreRpcError({"code": -32001, "message": "control_timeout"}),
    NteCoreProtocolError("invalid snapshot metadata"),
    NteCoreTimeoutError("native.snapshot.refresh", 60),
])
def test_optional_snapshot_failure_keeps_capture_and_does_not_repeat(error):
    core = FakeNativeCore()
    session = NativeGameSession(lambda: core, lambda _: None)
    lease = session.battle_client()
    try:
        lease.start_capture(profile="combat")
        record = {"native_capture": {"scopeAttempts": {"upper": attempt()}}}
        with patch("src.integrations.native_battle_snapshot.freeze_native_battle_snapshot", side_effect=error) as read:
            frozen = lease.observe_battle_scopes(record)
            assert frozen["scopes"]["upper"]["snapshot"]["state"] == "unavailable"
            assert lease.observe_battle_scopes(record) is None
            assert read.call_count == 1
            assert session.battle_active
            assert core.is_running
        record["native_capture"]["scopeAttempts"]["lower"] = attempt("2")
        with patch("src.integrations.native_battle_snapshot.freeze_native_battle_snapshot", return_value=native_snapshot()):
            next_half = lease.observe_battle_scopes(record)
            assert next_half["scopes"]["lower"]["snapshot"]["state"] == "observed"
            assert next_half["scopes"]["upper"]["snapshot"]["state"] == "unavailable"
    finally:
        lease.close()
        session.close()


@pytest.mark.parametrize("error", [NteCoreProcessError("closed"), PermissionError("revoked")])
def test_transport_loss_and_revoked_permission_are_not_hidden(error):
    core = FakeNativeCore()
    session = NativeGameSession(lambda: core, lambda _: None)
    lease = session.battle_client()
    try:
        with patch("src.integrations.native_battle_snapshot.freeze_native_battle_snapshot", side_effect=error):
            with pytest.raises(type(error)):
                lease.observe_battle_scopes({"native_capture": {"scopeAttempts": {"combat": attempt()}}})
    finally:
        lease.close()
        session.close()
