# 验证出场切换不丢失本场配装，真实队伍及配置变化仍拒绝。
from copy import deepcopy
from unittest.mock import patch

import pytest

from src.services.native_battle_scopes import select_scope_builds, validate_first_hit
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_battle_capture_build_freeze import capture, finish, native_snapshot, scoped  # noqa: F401


def observed_team():
    snapshot = native_snapshot()
    actor = {"slotIndex": 0, "objectKey": "10:20", "DefaultCharacterID": "1072"}
    snapshot["domains"]["team"].update(
        revision="2", domainKey="world-1:1/controller-2:2/ps-3:3/clone-fixture",
        observedUnixUs="2000000",
        records=[{"EquippedPlayers": [actor], "CharacterItems": [{"slotIndex": 0, "ItemID": "1072"}]}],
    )
    changes = {d: {"revision": "1", "dirty": False} for d in snapshot["domains"]}
    first = {
        "kind": "combat_context", "observedUnixUs": "500000",
        "roots": {"world": "1:1", "controller": "2:2", "ps": "3:3", "pawn": "10:20"},
        "team": {"source": "PlayerState.EquippedPlayers", "enumerationComplete": True, "actors": [actor]},
        "snapshotChanges": deepcopy(changes),
    }
    later = deepcopy(first)
    later["observedUnixUs"] = "2000000"
    later["roots"]["pawn"] = "11:21"
    later["snapshotChanges"]["team"] = {"revision": "3", "dirty": True}
    attempt = {"attemptId": "1", "firstUnixSeconds": 1.0, "lastUnixSeconds": 3.0,
               "firstChanges": changes, "changes": [later["snapshotChanges"]]}
    record = {"native_capture": {"providerId": "fixture", "droppedContexts": "0",
              "contextEvents": [first, later], "scopeAttempts": {"combat": attempt}}}
    return snapshot, attempt, record


def test_on_field_switch_preserves_first_hit_and_final_build():
    snapshot, attempt, record = observed_team()
    assert validate_first_hit(snapshot, attempt, record) is None
    entry = {"attempt_id": "1", "snapshot": snapshot, "profiles": {"1072": {"character_level": 70}},
             "stat_snapshots": {"1072": []}, "equipment": []}
    result, reason = select_scope_builds({"native_scope_builds": {"combat": entry}}, record)
    assert reason is None
    assert result["profiles"][1072]["character_level"] == 70


def test_live_lease_passes_context_evidence_to_first_hit_validation():
    from src.services.native_game_session import NativeGameSession
    from tests.test_native_game_session import FakeNativeCore

    snapshot, _, record = observed_team()
    core = FakeNativeCore()
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.battle_client()
    try:
        with patch("src.integrations.native_battle_snapshot.freeze_native_battle_snapshot", return_value=snapshot):
            lease.start_capture(profile="combat")
            saved = lease.observe_battle_scopes(record)
        assert saved["scopes"]["combat"]["snapshot"]["state"] == "observed"
        assert "first_hit_configuration_unverified" not in snapshot["missing"]
    finally:
        lease.close()
        session.close()


@pytest.mark.parametrize("change", ["role", "instance", "slot", "incomplete", "roots", "provider", "dropped", "missing_context"])
def test_real_team_change_or_missing_proof_remains_unavailable(change):
    snapshot, attempt, record = observed_team()
    native = record["native_capture"]
    later = native["contextEvents"][-1]
    if change in {"role", "instance", "slot"}:
        key, value = {"role": ("DefaultCharacterID", "1004"), "instance": ("objectKey", "10:21"),
                      "slot": ("slotIndex", 1)}[change]
        later["team"]["actors"][0][key] = value
    elif change == "incomplete":
        later["team"]["enumerationComplete"] = False
    elif change == "roots":
        later["roots"]["ps"] = "3:4"
    elif change == "provider":
        native["providerId"] = "other"
    elif change == "dropped":
        native["droppedContexts"] = "1"
    else:
        native["contextEvents"].pop()
    assert validate_first_hit(snapshot, attempt, record) == "first_hit_configuration_unverified"


@pytest.mark.parametrize("domain", ["character"])
def test_team_proof_never_bypasses_other_domain_versions(domain):
    snapshot, attempt, record = observed_team()
    snapshot["domains"][domain]["revision"] = "2"
    assert validate_first_hit(snapshot, attempt, record) == "first_hit_configuration_unverified"


def refreshed_character():
    snapshot, attempt, record = observed_team()
    current = snapshot["domains"]["character"]
    current.update(revision="2", observedUnixUs="1500000", enumerationComplete=True,
                   domainKey="world-1:1/controller-2:2/ps-3:3/inventory-4:4/clone-entered",
                   records=[{"ItemID": "1072", "CharacterLevel": 70, "Equipments": [42]}])
    previous = deepcopy(current)
    previous.update(revision="1", observedUnixUs="400000",
                    domainKey="world-1:1/controller-2:2/ps-3:3/inventory-4:4/clone-before")
    snapshot["prior_character_observation"] = previous
    attempt["changes"][0]["character"] = {"revision": "2", "dirty": False}
    return snapshot, attempt, record


def test_entry_refresh_accepts_identical_observed_character_configuration():
    from src.services.native_battle_scopes import scope_changed
    snapshot, attempt, record = refreshed_character()
    assert scope_changed(snapshot, attempt, record) is None


@pytest.mark.parametrize("change", ["equipment", "level", "provider", "roots", "time", "incomplete", "reference"])
def test_entry_refresh_requires_matching_configuration_evidence(change):
    snapshot, attempt, record = refreshed_character()
    previous = snapshot["prior_character_observation"]
    if change == "equipment":
        previous["records"][0]["Equipments"] = [43]
    elif change == "level":
        previous["records"][0]["CharacterLevel"] = 69
    elif change == "provider":
        previous["providerId"] = "other"
    elif change == "roots":
        previous["domainKey"] = previous["domainKey"].replace("ps-3:3", "ps-3:4")
    elif change == "time":
        previous["observedUnixUs"] = "1800000"
    elif change == "incomplete":
        previous["enumerationComplete"] = False
    else:
        attempt["firstSnapshotRefs"] = {"character": {"providerId": "other"}}
    assert validate_first_hit(snapshot, attempt, record) == "first_hit_configuration_unverified"


@pytest.mark.parametrize("domain", ["character"])
def test_midbattle_other_domain_change_still_invalidates_build(domain):
    from src.services.native_battle_scopes import scope_changed

    snapshot, attempt, record = observed_team()
    attempt["changes"][0][domain] = {"revision": "2", "dirty": True}
    assert scope_changed(snapshot, attempt, record) == "native_scope_configuration_changed"


def test_selected_roster_and_runtime_roster_must_agree():
    snapshot, attempt, record = observed_team()
    snapshot["domains"]["team"]["records"][0]["CharacterItems"][0]["ItemID"] = "1004"
    assert validate_first_hit(snapshot, attempt, record) == "first_hit_configuration_unverified"


@pytest.mark.parametrize("domain", ["inventory", "environment"])
def test_non_panel_domain_changes_do_not_discard_frozen_role_build(domain):
    from src.services.native_battle_scopes import scope_changed
    snapshot, attempt, record = observed_team()
    snapshot["domains"][domain]["revision"] = "9"
    attempt["changes"][0][domain] = {"revision": "10", "dirty": True}
    assert validate_first_hit(snapshot, attempt, record) is None
    assert scope_changed(snapshot, attempt, record) is None


def test_restore_empty_calculation_copy_uses_saved_equipment_and_is_idempotent(capture):
    service, deps, _, _ = capture
    snapshot, _, record = observed_team()
    with patch.object(service, "_resolve_character_stat_snapshots", return_value={}):
        service.bind_runtime_snapshot(capture_operation_id="capture", snapshot=scoped(snapshot))
    outcome = finish(service, record)
    with UserDataDao(deps.user_database_path) as dao:
        # Model an old report whose raw scope evidence survived but whose
        # materialized calculation copy is missing, independent of save policy.
        dao._db().execute("DELETE FROM battle_character_build_snapshot WHERE battle_record_id=?",
                          (outcome.battle_record_id,))
        dao._db().commit()
        before = dao.load_finalized_battle_capture_record(outcome.battle_record_id)
    assert service.restore_missing_native_build(outcome.battle_record_id)
    assert not service.restore_missing_native_build(outcome.battle_record_id)
    with UserDataDao(deps.user_database_path) as dao:
        build = dao.load_battle_build_snapshot(outcome.battle_record_id)
        after = dao.load_finalized_battle_capture_record(outcome.battle_record_id)
        assert build["source_inventory_snapshot_id"] is None
        assert build["characters"][0]["equipment"][0]["uid_serial"] == 202
        assert before["native_capture"] == after["native_capture"]
        assert "calc_build_validation" not in after


def test_on_field_switch_materializes_saved_build_without_changing_account(capture):
    service, deps, inventory_id, _ = capture
    snapshot, _, record = observed_team()
    with patch.object(service, "_resolve_character_stat_snapshots", return_value={}):
        service.bind_runtime_snapshot(capture_operation_id="capture", snapshot=scoped(snapshot))
    outcome = finish(service, record)
    with UserDataDao(deps.user_database_path) as dao:
        build = dao.load_battle_build_snapshot(outcome.battle_record_id)
        assert outcome.warning_message is None
        assert build["characters"][0]["character_id"] == 1072
        assert build["characters"][0]["equipment"][0]["uid_serial"] == 202
        assert dao.latest_native_inventory_snapshot_id() == inventory_id
        assert dao.list_character_profiles(include_inactive=True) == []


def pending_refresh():
    snapshot, attempt, record = observed_team()
    for domain in ("inventory", "environment"):
        snapshot["domains"][domain].update(
            domainKey="world-1:1/controller-2:2/ps-3:3/clone-fixture", observedUnixUs="2500000")
        attempt["changes"][0][domain]["dirty"] = True
    snapshot["inventory_projection"]["domainKey"] = snapshot["domains"]["inventory"]["domainKey"]
    return snapshot, attempt, record


def test_first_poll_missing_context_does_not_permanently_reject_saved_build(capture):
    from src.services.native_game_session import NativeGameSession
    from tests.test_native_game_session import FakeNativeCore

    service, deps, _, _ = capture
    snapshot, _, record = pending_refresh()
    first_poll = deepcopy(record)
    first_poll["native_capture"]["contextEvents"] = []
    session = NativeGameSession(FakeNativeCore, lambda _cap: None)
    lease = session.battle_client()
    try:
        lease.start_capture(profile="combat")
        with patch("src.integrations.native_battle_snapshot.freeze_native_battle_snapshot", return_value=deepcopy(snapshot)):
            assert lease.observe_battle_scopes(first_poll) is None
        frozen = lease.observe_battle_scopes(record, final=True)
        with patch.object(service, "_resolve_character_stat_snapshots", return_value={}):
            service.bind_runtime_snapshot(capture_operation_id="capture", snapshot=frozen)
        outcome = finish(service, record)
        with UserDataDao(deps.user_database_path) as dao:
            build = dao.load_battle_build_snapshot(outcome.battle_record_id)
            assert outcome.warning_message is None
            assert len(build["characters"]) == 1
            assert build["characters"][0]["equipment"][0]["uid_serial"] == 202
    finally:
        lease.close()
        session.close()




@pytest.mark.parametrize("condition", ["same_epoch", "new_epoch", "after_read", "missing_time", "missing_context", "provider"])
def test_dirty_cache_is_only_resolved_by_same_epoch_later_read(condition):
    from src.services.native_battle_scopes import scope_changed
    snapshot, attempt, record = pending_refresh()
    snapshot["domains"]["character"].update(domainKey="world-1:1/controller-2:2/ps-3:3/clone-fixture", observedUnixUs="2500000")
    attempt["changes"][0]["character"]["dirty"] = True
    if condition == "new_epoch":
        attempt["changes"][0]["character"]["revision"] = "2"
    elif condition == "after_read":
        snapshot["domains"]["character"]["observedUnixUs"] = "1500000"
    elif condition == "missing_time":
        snapshot["domains"]["character"].pop("observedUnixUs")
    elif condition == "missing_context":
        record["native_capture"]["contextEvents"].pop()
    elif condition == "provider":
        record["native_capture"]["providerId"] = "other"
    assert (scope_changed(snapshot, attempt, record) is None) == (condition == "same_epoch")
