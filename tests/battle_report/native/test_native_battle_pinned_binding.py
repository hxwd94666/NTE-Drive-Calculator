# 验证固定首击快照先暂存后冻结，补读、重开和缺项结算不覆盖已保存事实。
from tests.battle_report.capture.test_battle_capture_build_freeze import capture as capture
from copy import deepcopy
from unittest.mock import patch

import pytest

from src.services.battle_report.inputs.scopes import observe_native_scopes
from src.services.native_session.session import NativeGameSession
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.battle_report.capture.test_native_battle_equipped import equipped_source
from tests.battle_report.inputs.helpers import attempt
from tests.battle_report.capture.test_battle_capture_build_freeze import finish
from tests.native_session.test_native_game_session import FakeNativeCore


@pytest.fixture
def pinned_lease():
    core = FakeNativeCore()
    core.hello_result["capabilities"].append("snapshot.pinned.v1")
    session = NativeGameSession(lambda: core, lambda _: None)
    lease = session.battle_client()
    lease.start_capture(profile="combat")
    try:
        yield lease
    finally:
        lease.close()
        session.close()


def pinned_snapshot():
    return {**equipped_source(), "retention": "dll_pinned"}


def record_for(**attempts):
    return {"native_capture": {"providerId": "fixture", "scopeAttempts": attempts}}


def partial_snapshot():
    snapshot = pinned_snapshot()
    snapshot["domains"].pop("character")
    snapshot.pop("character_projection")
    snapshot["missing"] = ["character_first_hit_unavailable"]
    return snapshot


def test_partial_then_complete_pinned_snapshot_saves_without_overwrite(capture, pinned_lease):
    service, deps, inventory_id, _ = capture
    record = record_for(combat=attempt())
    with patch("src.integrations.native_core.snapshots.native_battle_snapshot.read_first_hit_snapshot",
               side_effect=[partial_snapshot(), pinned_snapshot()]):
        observe_native_scopes(pinned_lease, service, "capture", record)
        with UserDataDao(deps.user_database_path) as dao:
            pending_build = dao.load_battle_capture_build("capture")
        # 走真实绑定与 DAO，复现第二次读取时曾经抛出的不可覆盖异常。
        observe_native_scopes(pinned_lease, service, "capture", record)
    assert not pending_build.get("native_scope_builds")
    outcome = finish(service, record)
    assert outcome.status == "saved"
    assert outcome.warning_message is None
    with UserDataDao(deps.user_database_path) as dao:
        build = dao.load_battle_build_snapshot(outcome.battle_record_id)
        assert build["characters"][0]["character_level"] == 70
        assert build["characters"][0]["equipment"][0]["uid_serial"] == 202
        assert dao.latest_native_inventory_snapshot_id() == inventory_id


def test_pending_half_is_not_published_alongside_ready_half(pinned_lease):
    record = record_for(upper=attempt(), lower=attempt("2"))
    with patch("src.integrations.native_core.snapshots.native_battle_snapshot.read_first_hit_snapshot",
               side_effect=[partial_snapshot(), pinned_snapshot(), pinned_snapshot()]) as read:
        first = pinned_lease.observe_battle_scopes(record)
        assert set(first["scopes"]) == {"lower"}
        second = pinned_lease.observe_battle_scopes(record)
        assert set(second["scopes"]) == {"upper", "lower"}
        assert second["scopes"]["lower"] == first["scopes"]["lower"]
        assert pinned_lease.observe_battle_scopes(record, final=True) is None
        assert read.call_count == 3


def test_final_partial_evidence_saves_measurements_and_explicit_fallback(capture, pinned_lease):
    service, deps, _, _ = capture
    record = record_for(combat=attempt())
    with patch("src.integrations.native_core.snapshots.native_battle_snapshot.read_first_hit_snapshot", return_value=partial_snapshot()):
        observe_native_scopes(pinned_lease, service, "capture", record)
        observe_native_scopes(pinned_lease, service, "capture", record, final=True)
    with UserDataDao(deps.user_database_path) as dao:
        frozen = dao.load_battle_capture_build("capture")["native_scope_builds"]
    assert frozen["combat"]["snapshot"]["missing"] == ["character_first_hit_unavailable"]
    with patch("src.integrations.native_core.snapshots.native_battle_snapshot.read_first_hit_snapshot") as read:
        assert pinned_lease.observe_battle_scopes(record, final=True) is None
        read.assert_not_called()
    outcome = finish(service, record)
    assert outcome.status == "saved"
    assert "数据库配置补齐" in outcome.warning_message
    with UserDataDao(deps.user_database_path) as dao:
        assert dao.load_battle_capture_build("capture")["settlement_fallback"]["1072"]["source"] == "account_settlement_fallback"


def test_frozen_attempt_is_not_reread_when_later_context_no_longer_validates(pinned_lease):
    record = record_for(combat=attempt())
    with patch("src.integrations.native_core.snapshots.native_battle_snapshot.read_first_hit_snapshot", return_value=pinned_snapshot()):
        first = pinned_lease.observe_battle_scopes(record)
    # 后续上下文只影响结算对证据的可用性判断，不授权改写首击事实。
    record["native_capture"]["scopeAttempts"]["combat"]["firstChanges"]["character"]["revision"] = "2"
    with patch("src.integrations.native_core.snapshots.native_battle_snapshot.read_first_hit_snapshot") as read:
        assert pinned_lease.observe_battle_scopes(record, final=True) is None
        read.assert_not_called()
    assert first["scopes"]["combat"]["snapshot"]["domains"]["character"]["revision"] == "1"


@pytest.mark.parametrize("was_ready", [False, True])
def test_restart_clears_only_replaced_attempt(pinned_lease, was_ready):
    record = record_for(upper=attempt(), lower=attempt("2"))
    with patch("src.integrations.native_core.snapshots.native_battle_snapshot.read_first_hit_snapshot",
               side_effect=[pinned_snapshot(), pinned_snapshot() if was_ready else partial_snapshot()]):
        first = pinned_lease.observe_battle_scopes(record)
    del record["native_capture"]["scopeAttempts"]["lower"]
    removed = pinned_lease.observe_battle_scopes(record)
    if was_ready:
        assert removed["scopes"] == {"upper": first["scopes"]["upper"]}
    else:
        assert removed is None
    record["native_capture"]["scopeAttempts"]["lower"] = attempt("3")
    with patch("src.integrations.native_core.snapshots.native_battle_snapshot.read_first_hit_snapshot", return_value=pinned_snapshot()) as read:
        restarted = pinned_lease.observe_battle_scopes(record)
    assert restarted["scopes"]["upper"] == first["scopes"]["upper"]
    assert restarted["scopes"]["lower"]["attempt_id"] == "3"
    assert read.call_count == 1


def test_pending_partial_survives_transient_read_failure(pinned_lease):
    record = record_for(combat=attempt())
    unavailable = {"state": "unavailable", "domains": {}, "missing": ["first_hit_snapshot_read_failed"]}
    with patch("src.integrations.native_core.snapshots.native_battle_snapshot.read_first_hit_snapshot",
               side_effect=[partial_snapshot(), unavailable]):
        assert pinned_lease.observe_battle_scopes(record) is None
        result = pinned_lease.observe_battle_scopes(record, final=True)
    frozen = result["scopes"]["combat"]["snapshot"]
    assert frozen["domains"]["team"] == partial_snapshot()["domains"]["team"]
    assert frozen["missing"] == ["character_first_hit_unavailable"]


@pytest.mark.parametrize("missing", ["environment", "projection"])
def test_optional_environment_does_not_block_but_missing_equipment_stays_pending(pinned_lease, missing):
    snapshot = pinned_snapshot()
    if missing == "environment":
        del snapshot["domains"]["environment"]
    else:
        snapshot["character_projection"]["battleEquipment"]["complete"] = False
    with patch("src.integrations.native_core.snapshots.native_battle_snapshot.read_first_hit_snapshot", return_value=deepcopy(snapshot)):
        result = pinned_lease.observe_battle_scopes(record_for(combat=attempt()))
    if missing == "environment":
        assert result["scopes"]["combat"]["snapshot"]["state"] == "observed"
    else:
        assert result is None
