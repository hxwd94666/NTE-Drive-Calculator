# 验证采集开始冻结、原生副本独立落库、场中失效与失败重试边界。
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
from unittest.mock import patch

import pytest

from src.observability import OperationContext
from src.services.battle_report_persistence_service import BattleReportPersistenceDependencies, BattleReportPersistenceService
from src.storage.sqlite.user_data_dao import UserDataDao
from src.storage.sqlite.user_data_support import UserDataError
from tests.test_battle_axis_dao import _equipped_item, _profile, _snapshot
from tests.test_battle_report_persistence_service import _summary


@pytest.fixture
def capture(tmp_path, request):
    path = tmp_path / "account.sqlite3"
    with UserDataDao(path, account_id="fixture") as dao:
        snapshot_id = dao.import_inventory_snapshot(_snapshot(1, [_equipped_item(101, 11, 1072)]))
    dependencies = BattleReportPersistenceDependencies("fixture", path, 3, tmp_path / "static.sqlite3")
    service = BattleReportPersistenceService(dependencies=dependencies, context_is_current=lambda _: True,
                                            operation_context=OperationContext.create("battle_report"))
    profiles = {1072: _profile(1072, "fork_Test")}
    with patch("src.services.battle_report_persistence_service.StaticGameDataDao") as static, \
            patch.object(service, "_load_effective_profiles", return_value=profiles), \
            patch.object(service, "_resolve_character_stat_snapshots", side_effect=lambda **kw: {cid: [{"source_group": "resolved", "property_id": "CritDamageBase", "display_name": "暴击伤害", "value": 0.5, "is_percent": True, "ordinal": 0}] for cid in kw["character_ids"]}):
        static.return_value.__enter__.return_value.summary.return_value = {"dataset": {"dataset_id": "fixture-v1"}, "schema_version": 32}
        service.begin_capture(capture_operation_id="capture", captured_at_utc="2026-09-11T00:00:00Z", capture_source=getattr(request, "param", "native"))
        yield service, dependencies, snapshot_id, profiles


def scoped(native):
    return {"state": "scoped", "scopes": {"combat": {"attempt_id": "1", "snapshot": native}}}


def finish(service, record=None):
    if record is None:
        record = {}
    with UserDataDao(service._dependencies.user_database_path) as dao:
        build = dao.load_battle_capture_build("capture")
    if "native_scope_builds" in build:
        native = record.setdefault("native_capture", {})
        native.setdefault("scopeAttempts", {"combat": {"attemptId": "1", "firstChanges": {
            d: {"revision": "1", "dirty": False} for d in ("character", "inventory", "team", "environment")},
            "changes": [e["snapshotChanges"] for e in native.get("contextEvents", [])]}})
    summary = _summary()
    summary = replace(summary, characters=(replace(summary.characters[0], character_id=1072),))
    return service.finalize_summary(
        capture_operation_id="capture", summary=summary, raw_summary_payload={"total_damage": 120, "total_hits": 12},
        captured_at_utc="2026-09-11T00:00:00Z", finalized_at_utc="2026-09-11T00:00:10Z", raw_record_payload=record,
    )


def native_snapshot():
    domains = {domain: {"providerId": "fixture", "domainKey": domain, "snapshotId": domain + "-1",
                        "revision": "1", "dirty": False, "complete": False, "sourceCoverage": "unknown", "records": []}
               for domain in ("character", "inventory", "team", "environment")}
    domains["team"]["records"] = [{"CharacterItems": [{"ItemID": "1072"}]}]
    inventory = {**domains["inventory"], "projectionComplete": True, "collectionComplete": True,
                 "characterRefsComplete": True, "collectionScope": "EQUIP", "items": [_equipped_item(202, 22, 1072)]}
    character = {**domains["character"], "profiles": [{"character_id": 1072, "character_level": 70, "breakthrough_stage": 5}]}
    return {"schema_version": 1, "state": "observed", "binding": "first_hit_revision",
            "selection": {"kind": "team_subset", "character_ids": [1072]},
            "domains": domains, "inventory_projection": inventory, "character_projection": character, "missing": []}


@pytest.mark.parametrize("capture", ["packet"], indirect=True)
def test_packet_end_uses_observed_roles_and_settlement_database_then_freezes_retry(capture):
    service, deps, _, profiles = capture
    profiles[1072]["character_level"] = 60
    profiles[1072]["breakthrough_stage"] = 4
    with UserDataDao(deps.user_database_path) as dao:
        snapshot_id = dao.import_inventory_snapshot(_snapshot(2, [_equipped_item(999, 99, 1072)]))
    with patch("src.services.battle_report_persistence_service.StaticGameDataDao") as static, \
            patch.object(service, "_load_effective_profiles", return_value=profiles), \
            patch.object(service, "_resolve_character_stat_snapshots", return_value={}) as stats:
        static.return_value.__enter__.return_value.summary.return_value = {"dataset": {"dataset_id": "fixture-v1"}, "schema_version": 32}
        outcome = finish(service)
    assert stats.call_args.kwargs["character_ids"] == (1072,)
    with UserDataDao(deps.user_database_path) as dao:
        build = dao.load_battle_build_snapshot(outcome.battle_record_id)
        assert build["source_inventory_snapshot_id"] == snapshot_id
        assert build["characters"][0]["character_level"] == 60
        assert build["characters"][0]["equipment"][0]["uid_serial"] == 999
        assert dao.load_battle_capture_build("capture")["freeze_phase"] == "settlement_database"
        dao.import_inventory_snapshot(_snapshot(3, [_equipped_item(888, 88, 1072)]))
    with patch.object(service, "_freeze_capture_build", side_effect=AssertionError("retry read")):
        retry = finish(service)
    assert retry.battle_record_id == outcome.battle_record_id
    with UserDataDao(deps.user_database_path) as dao:
        assert dao.load_battle_build_snapshot(outcome.battle_record_id) == build


def test_native_projection_freezes_actual_equipment_without_importing_inventory_or_account_profiles(capture):
    service, deps, snapshot_id, _ = capture
    native = native_snapshot()
    with patch.object(service, "_resolve_character_stat_snapshots", return_value={}) as stats:
        service.bind_runtime_snapshot(capture_operation_id="capture", snapshot=scoped(native))
    assert stats.call_args.kwargs["profiles"][1072]["character_level"] == 70
    native["domains"]["inventory"]["revision"] = "999"
    outcome = finish(service)
    assert outcome.warning_message is None
    with UserDataDao(deps.user_database_path) as dao:
        build = dao.load_battle_build_snapshot(outcome.battle_record_id)
        assert build["source_inventory_snapshot_id"] is None
        assert build["characters"][0]["equipment"][0]["uid_serial"] == 202
        assert build["characters"][0]["character_level"] == 70
        assert dao.latest_native_inventory_snapshot_id() == snapshot_id
        assert dao.list_character_profiles(include_inactive=True) == []
        assert dao.load_battle_capture_build("capture")["native_runtime_snapshot"]["scopes"]["combat"]["snapshot"]["domains"]["inventory"]["revision"] == "1"


def test_native_awakening_selection_is_frozen_separately_from_level(capture):
    _, deps, _, _ = capture
    deps = replace(deps, static_database_path=Path('data/game_static.sqlite3').resolve())
    service = BattleReportPersistenceService(dependencies=deps, context_is_current=lambda _: True,
                                            operation_context=OperationContext.create('battle_report'))
    native = native_snapshot()
    native['character_projection']['profiles'][0].update(
        awakening_level=6, awakening_selection_initialized=True, selected_awaken_effect_ids=['Effect1'])
    with patch.object(service, '_resolve_character_stat_snapshots', return_value={}):
        service.bind_runtime_snapshot(capture_operation_id='capture', snapshot=scoped(native))
    native['character_projection']['profiles'][0]['selected_awaken_effect_ids'].clear()
    outcome = finish(service)
    with UserDataDao(deps.user_database_path) as dao:
        build = dao.load_battle_build_snapshot(outcome.battle_record_id)
        character = build['characters'][0]
        assert character['awakening_level'] == 6
        assert character['profile']['selected_awaken_effect_ids'] == ['Effect1']
        assert character['profile']['awakening_selection_initialized'] is True
        assert dao.get_native_character_profile_observation(1072) is None


@pytest.mark.parametrize("flag", [{"bIsTemporary": True}, {"source": "TrialCharacterItems"}, {"containerType": 24}])
def test_trial_identity_is_archived_and_database_fallback_is_explicit(capture, flag):
    service, deps, snapshot_id, _ = capture
    native = native_snapshot()
    native["domains"]["character"]["records"] = [{"kind": "HTCharacterItem", "ItemID": "1072", **flag}]
    service.bind_runtime_snapshot(capture_operation_id="capture", snapshot=scoped(native))
    outcome = finish(service)
    assert "数据库配置补齐" in outcome.warning_message
    with UserDataDao(deps.user_database_path) as dao:
        build = dao.load_battle_build_snapshot(outcome.battle_record_id)
        assert build["characters"][0]["profile"]["capture_equipment_source"] == "account_settlement_fallback"
        assert dao.load_battle_capture_build("capture")["settlement_fallback"]["1072"]["reason"] == "temporary_character_projection_unavailable"
        assert dao.load_battle_capture_build("capture")["native_runtime_snapshot"] == scoped(native)
        assert dao.latest_native_inventory_snapshot_id() == snapshot_id


@pytest.mark.parametrize("change", [{"dirty": True}, {"revision": "2"}])
def test_midbattle_change_retains_start_observation_but_does_not_replay_old_build(capture, change):
    service, deps, _, _ = capture
    native = native_snapshot()
    with patch.object(service, "_resolve_character_stat_snapshots", return_value={}):
        service.bind_runtime_snapshot(capture_operation_id="capture", snapshot=scoped(native))
    changes = {domain: {"revision": "1", "dirty": False} for domain in native["domains"]}
    changes["team"].update(change)
    record = {"native_capture": {"contextEvents": [{"kind": "combat_context", "snapshotChanges": changes}]}}
    outcome = finish(service, record)
    with UserDataDao(deps.user_database_path) as dao:
        build = dao.load_battle_build_snapshot(outcome.battle_record_id)
        assert build["characters"][0]["profile"]["capture_equipment_source"] == "account_settlement_fallback"
        assert dao.load_battle_capture_build("capture")["settlement_fallback"]["1072"]["reason"] == "native_scope_configuration_changed"
        raw = json.loads(dao._db().execute("SELECT raw_record_json FROM battle_axis_capture").fetchone()[0])
        assert raw["native_capture"] == record["native_capture"]
        assert raw["calc_capture_context"]["native_runtime_snapshot"] == scoped(native)


def test_binding_is_idempotent_but_replacing_snapshot_is_rejected(capture):
    service, deps, _, _ = capture
    native = {"state": "unavailable", "domains": {}, "missing": ["fixture"]}
    service.bind_runtime_snapshot(capture_operation_id="capture", snapshot=scoped(native))
    service.bind_runtime_snapshot(capture_operation_id="capture", snapshot=scoped(native))
    changed = scoped({**native, "state": "source_changed"})
    with pytest.raises(UserDataError, match="不能覆盖"):
        service.bind_runtime_snapshot(capture_operation_id="capture", snapshot=changed)
    with UserDataDao(deps.user_database_path) as dao:
        assert dao.load_battle_capture_build("capture")["native_runtime_snapshot"] == scoped(native)
    service.discard_capture(capture_operation_id="capture")
    with UserDataDao(deps.user_database_path) as dao:
        assert dao.load_battle_capture_build("capture") is None


def test_unprojectable_native_growth_preserves_measurements_as_unknown(capture):
    service, deps, _, _ = capture
    native = native_snapshot()
    native["character_projection"]["profiles"][0].update(character_level=70, breakthrough_stage=0)
    service.bind_runtime_snapshot(capture_operation_id="capture", snapshot=scoped(native))
    outcome = finish(service)
    assert outcome.status == "saved"
    with UserDataDao(deps.user_database_path) as dao:
        restored = dao.load_battle_build_snapshot(outcome.battle_record_id)["characters"][0]
        assert restored["character_level"] == 80
        assert restored["equipment"][0]["uid_serial"] == 202
        assert dao.load_battle_capture_build("capture")["native_runtime_snapshot"] == scoped(native)


@pytest.mark.parametrize("capture", ["packet"], indirect=True)
def test_failed_packet_save_retries_the_same_durable_settlement_input(capture):
    service, deps, snapshot_id, profiles = capture
    with UserDataDao(deps.user_database_path) as dao:
        frozen = deepcopy(dao.load_battle_capture_build("capture"))
        frozen.update(profiles=profiles, snapshot_id=snapshot_id,
                      equipment=dao.list_inventory_items(snapshot_id, equipped=True),
                      equipment_source="capture_start_account_snapshot")
    with patch.object(service, "_freeze_capture_build", return_value=frozen), patch.object(
            UserDataDao, "insert_auto_summary_snapshot", side_effect=UserDataError("save failed")):
        with pytest.raises(UserDataError, match="save failed"):
            finish(service)
    with UserDataDao(deps.user_database_path) as dao:
        before = dao.load_battle_capture_build("capture")
        assert before["freeze_phase"] == "settlement_database"
        dao.import_inventory_snapshot(_snapshot(2, [_equipped_item(999, 99, 1072)]))
        with pytest.raises(UserDataError, match="代次失效"):
            dao.freeze_battle_settlement_build("capture", frozen, account_generation=deps.generation + 1)
    with patch.object(service, "_freeze_capture_build", side_effect=AssertionError("late input")):
        outcome = finish(service)
    with UserDataDao(deps.user_database_path) as dao:
        assert dao.load_battle_build_snapshot(outcome.battle_record_id)["characters"][0]["equipment"][0]["uid_serial"] == 101
        assert dao.load_battle_capture_build("capture") == before


def test_native_missing_record_metadata_uses_labeled_settlement_database(capture):
    service, deps, _, _ = capture
    outcome = finish(service, {"source": "native_dll"})
    with UserDataDao(deps.user_database_path) as dao:
        saved = dao.load_battle_build_snapshot(outcome.battle_record_id)
        assert saved["characters"][0]["equipment"][0]["uid_serial"] == 101
        assert saved["characters"][0]["profile"]["capture_equipment_source"] == "account_settlement_fallback"
        assert dao.load_battle_capture_build("capture")["settlement_fallback"]["1072"]["reason"] == "native_first_hit_snapshot_missing"


def test_missing_native_equipment_keeps_observed_growth_and_labels_only_equipment_fallback(capture):
    service, deps, _, _ = capture
    native = native_snapshot()
    native.pop("inventory_projection")
    service.bind_runtime_snapshot(capture_operation_id="capture", snapshot=scoped(native))
    outcome = finish(service)
    with UserDataDao(deps.user_database_path) as dao:
        character = dao.load_battle_build_snapshot(outcome.battle_record_id)["characters"][0]
        frozen = dao.load_battle_capture_build("capture")
        assert character["character_level"] == 70
        assert character["equipment"][0]["uid_serial"] == 101
        assert frozen["settlement_fallback"]["1072"]["fields"] == ["equipment"]
        assert frozen["native_runtime_snapshot"] == scoped(native)


def test_failed_native_save_retries_frozen_database_input_and_rejects_late_binding(capture):
    service, deps, _, profiles = capture
    native = native_snapshot()
    native["character_projection"]["profiles"] = []
    service.bind_runtime_snapshot(capture_operation_id="capture", snapshot=scoped(native))
    with patch.object(UserDataDao, "insert_auto_summary_snapshot", side_effect=UserDataError("save failed")):
        with pytest.raises(UserDataError, match="save failed"):
            finish(service)
    with UserDataDao(deps.user_database_path) as dao:
        before = dao.load_battle_capture_build("capture")
        assert before["freeze_phase"] == "settlement_native"
        assert before["settlement_fallback"]["1072"]["fields"] == ["profile"]
        dao.import_inventory_snapshot(_snapshot(2, [_equipped_item(999, 99, 1072)]))
    profiles[1072]["character_level"] = 60
    with pytest.raises(UserDataError, match="结算输入已经冻结"):
        service.bind_runtime_snapshot(capture_operation_id="capture", snapshot=scoped(native))
    with patch.object(service, "_freeze_capture_build", side_effect=AssertionError("late database read")):
        outcome = finish(service)
    with UserDataDao(deps.user_database_path) as dao:
        character = dao.load_battle_build_snapshot(outcome.battle_record_id)["characters"][0]
        assert character["character_level"] == 80
        assert character["equipment"][0]["uid_serial"] == 202
        assert dao.load_battle_capture_build("capture") == before


def test_settlement_cannot_rewrite_original_native_evidence(capture):
    service, deps, _, _ = capture
    service.bind_runtime_snapshot(capture_operation_id="capture", snapshot=scoped(native_snapshot()))
    with UserDataDao(deps.user_database_path) as dao:
        before = dao.load_battle_capture_build("capture")
        changed = deepcopy(before)
        changed.update(freeze_phase="settlement_native", native_scope_builds={})
        with pytest.raises(UserDataError, match="不能替换原生首击证据"):
            dao.freeze_battle_settlement_build("capture", changed, account_generation=deps.generation)
        assert dao.load_battle_capture_build("capture") == before
