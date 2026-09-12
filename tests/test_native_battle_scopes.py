# 验证半场重开、第一击版本绑定、家具换算与独立正式副本。
from copy import deepcopy
from unittest.mock import patch

import pytest

from src.services.native_battle_scopes import select_scope_builds, validate_first_hit
from src.services.native_battle_world_bonus import native_world_bonus
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_battle_capture_build_freeze import capture, native_snapshot, finish  # noqa: F401


def attempt(sequence="1", revision="1"):
    return {"attemptId": sequence, "firstChanges": {d: {"revision": revision, "dirty": False}
            for d in ("character", "inventory", "team", "environment")}, "changes": []}


def test_first_hit_cannot_bind_later_configuration():
    assert validate_first_hit(native_snapshot(), attempt()) is None
    assert validate_first_hit(native_snapshot(), attempt(revision="2")) == "first_hit_configuration_unverified"
    assert validate_first_hit(native_snapshot(), {}) == "first_hit_configuration_unverified"


@pytest.mark.parametrize("lower_has_hit", [False, True])
def test_save_after_lower_switch_only_persists_halves_with_hits(capture, lower_has_hit):
    service, deps, _, _ = capture
    attempts = {"upper": attempt("1")}
    if lower_has_hit:
        attempts["lower"] = attempt("2")
    snapshots = {half: {"attempt_id": value["attemptId"], "snapshot": native_snapshot()}
                 for half, value in attempts.items()}
    with patch.object(service, "_resolve_character_stat_snapshots", return_value={}):
        service.bind_runtime_snapshot(capture_operation_id="capture",
                                      snapshot={"state": "scoped", "scopes": snapshots})
    record = {"abyss": {"detected": True, "active_half": "lower"},
              "native_capture": {"scopeAttempts": attempts}}
    with UserDataDao(deps.user_database_path) as dao:
        before = dao.load_battle_capture_build("capture")
    selected, reason = select_scope_builds(before, record)
    assert reason is None
    assert set(selected["native_scope_builds"]) == set(attempts)
    outcome = finish(service, record)
    assert outcome.warning_message is None
    with UserDataDao(deps.user_database_path) as dao:
        assert dao.load_battle_build_snapshot(outcome.battle_record_id) is not None
        saved = dao.load_battle_capture_build("capture")
        assert set(saved["native_scope_builds"]) == set(attempts)
        assert set(saved["native_runtime_snapshot"]["scopes"]) == set(attempts)


def test_lease_waits_for_first_hit_and_reads_once_per_surviving_attempt():
    from src.services.native_game_session import NativeGameSession
    from tests.test_native_game_session import FakeNativeCore
    core = FakeNativeCore()
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.battle_client()
    try:
        with patch("src.integrations.native_battle_snapshot.freeze_native_battle_snapshot", return_value=native_snapshot()) as read:
            lease.start_capture(profile="combat")
            read.assert_not_called()
            assert lease.observe_battle_scopes({"native_capture": {"scopeAttempts": {}}}) is None
            record = {"native_capture": {"scopeAttempts": {"upper": attempt()}}}
            assert lease.observe_battle_scopes(record)["scopes"]["upper"]["attempt_id"] == "1"
            assert lease.observe_battle_scopes(record) is None
            record["native_capture"]["scopeAttempts"]["lower"] = attempt("2")
            assert set(lease.observe_battle_scopes(record)["scopes"]) == {"upper", "lower"}
            record["native_capture"]["scopeAttempts"]["lower"] = attempt("3")
            saved = lease.observe_battle_scopes(record)
            assert saved["scopes"]["upper"]["attempt_id"] == "1"
            assert saved["scopes"]["lower"]["attempt_id"] == "3"
            assert read.call_count == 3
    finally:
        lease.close()
        session.close()


def test_permanent_unsaved_card_is_not_misclassified_as_trial():
    from src.services.battle_capture_build_context import native_build_unavailable_reason
    snapshot = native_snapshot()
    snapshot["domains"]["character"]["records"] = [{"bIsTemporary": False, "bUnSaved": True, "source": "CharacterItems"}]
    assert native_build_unavailable_reason(snapshot) is None


def test_ordinary_clone_restart_drops_temporary_build_until_next_attempt_hit():
    from src.services.native_game_session import NativeGameSession
    from tests.test_native_game_session import FakeNativeCore
    core = FakeNativeCore()
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.battle_client()
    try:
        with patch("src.integrations.native_battle_snapshot.freeze_native_battle_snapshot", return_value=native_snapshot()) as read:
            lease.start_capture(profile="combat")
            first = {"native_capture": {"scopeAttempts": {"combat": attempt("1")}}}
            assert lease.observe_battle_scopes(first)["scopes"]["combat"]["attempt_id"] == "1"
            waiting = {"native_capture": {"scopeAttempts": {}, "cloneAttempt": {"revision": "1"}}}
            assert lease.observe_battle_scopes(waiting)["scopes"] == {}
            assert lease.observe_battle_scopes(waiting) is None
            assert read.call_count == 1
            next_hit = {"native_capture": {"scopeAttempts": {"combat": attempt("9")}}}
            assert lease.observe_battle_scopes(next_hit)["scopes"]["combat"]["attempt_id"] == "9"
            assert read.call_count == 2
    finally:
        lease.close()
        session.close()


def test_restart_lower_rejects_old_snapshot_without_losing_upper():
    snapshot = native_snapshot()
    entry = {"attempt_id": "1", "snapshot": snapshot, "profiles": {"1072": {"character_level": 70}},
             "stat_snapshots": {"1072": []}, "equipment": []}
    build = {"native_scope_builds": {"upper": entry, "lower": {**entry, "attempt_id": "2"}}}
    result, reason = select_scope_builds(build, {"native_capture": {"scopeAttempts": {
        "upper": attempt(), "lower": attempt("3")}}})
    assert reason == "native_scope_snapshot_missing"
    assert set(result["native_scope_builds"]) == {"upper"}
    assert result["profiles"][1072]["character_level"] == 70
    assert build["native_scope_builds"]["lower"]["attempt_id"] == "2"


def test_world_bonus_uses_frozen_native_levels_without_mutating_account():
    snapshot = native_snapshot()
    snapshot["domains"]["environment"]["records"] = [
        {"kind": "furniture_raw", "FurnitureID": "SF_0011", "Level": 3, "bIsActivated": True},
        {"kind": "furniture_raw", "FurnitureID": "SF_0012", "Level": 4, "bIsActivated": True},
        {"kind": "environment_observation", "CurDivinationRewardArray": [{"RewardId": "fixture"}]},
    ]
    fallback = {"yaodao_attack_add": 20., "quantao_crit_damage": .04}
    values, evidence = native_world_bonus(snapshot, fallback)
    assert values == {"yaodao_attack_add": 6., "quantao_crit_damage": .016}
    assert fallback["yaodao_attack_add"] == 20
    assert evidence["witch_effect_state"] == "unverified"
    snapshot["domains"]["environment"]["records"].clear()
    assert len(evidence["environment_records"]) == 3


def test_unknown_furniture_keeps_explicit_fallback_source():
    values, evidence = native_world_bonus(native_snapshot(), {"yaodao_attack_add": 8., "quantao_crit_damage": .02})
    assert values["yaodao_attack_add"] == 8
    assert evidence["fields"]["yaodao_attack_add"]["source"] == "account_setting_fallback"


def test_restarted_attempt_replaces_staging_and_saves_new_equipment(capture):
    service, deps, _, _ = capture
    first = native_snapshot()
    second = deepcopy(first)
    second["inventory_projection"]["items"][0]["uid"]["serial"] = 777
    with patch.object(service, "_resolve_character_stat_snapshots", return_value={}):
        service.bind_runtime_snapshot(capture_operation_id="capture", snapshot={"state": "scoped", "scopes": {
            "combat": {"attempt_id": "old", "snapshot": first}}})
        service.bind_runtime_snapshot(capture_operation_id="capture", snapshot={"state": "scoped", "scopes": {}})
        service.bind_runtime_snapshot(capture_operation_id="capture", snapshot={"state": "scoped", "scopes": {
            "combat": {"attempt_id": "1", "snapshot": second}}})
    outcome = finish(service)
    with UserDataDao(deps.user_database_path) as dao:
        saved = dao.load_battle_build_snapshot(outcome.battle_record_id)
        assert saved["characters"][0]["equipment"][0]["uid_serial"] == 777
        assert dao.load_battle_capture_build("capture")["native_scope_builds"]["combat"]["attempt_id"] == "1"
