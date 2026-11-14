# 验证队伍增强轻量格式、负收益、整队同事务保存、故障回滚及删除所有权。
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3
from uuid import uuid4

import pytest

from src.domain.teams.enhancement import TeamResultCommit, TeamResultCommitEntry, validate_team_result_payload
from src.domain.teams.models import TeamDraft
from src.domain.teams.overview import SourceMode
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.teams.overview.team_overview_fixture import overview_case


# 来源、事务与账号失效属于核心边界，显式分层避免仅按文件名遗漏。
NTE_TEST_TIER = "core"


def payload(team, config, *, kind="optimized", before=100, after=105):
    return dict(version=1, team_revision=team.revision, character_ids=list(team.character_ids),
        source_mode=config.mode, source_revision=config.revision, source_bindings=[list(pair) for pair in config.bindings],
        scope=dict(record_id=1, scope="first", start_us=None, end_us=None), anchor_mode="original", result_kind=kind,
        baseline_value=before, candidate_value=after, relative_gain=after/before-1 if kind == "optimized" else None,
        coverage=None, search={"termination": "time_budget", "neighborhood_complete": False},
        provenance={"dataset_id": "fixture", "model_id": "frozen_axis_expected_v1"},
        source_input_digest="a"*64, baseline_build_sha256="b"*64)


def commit_for(dao, team, *, value=None, run_id=None):
    config = dao.get_team_source(team.team_id)
    value = value or payload(team, config)
    entry = TeamResultCommitEntry(team, config, tuple((str(key), key) for key in team.character_ids), validate_team_result_payload(value))
    return TeamResultCommit(run_id or uuid4().hex, "overview", "c"*64, (entry,))


def prepared(dao, slots):
    rows = []
    for character_id, slot_id in slots.items():
        plan = dao.get_loadout_slot(slot_id)["current_plan"]
        rows.append({key: plan[key] for key in ("name", "character_id", "source_snapshot_id", "status", "score", "assignments", "payload")}
                    | {"slot_id": slot_id})
    return rows


def test_actual_saved_plan_ids_and_result_are_committed_together(tmp_path):
    service, team, slots = overview_case(tmp_path)
    with UserDataDao(service.database_path) as dao:
        request = commit_for(dao, team)
        ids = dao.save_calculated_loadout_plans(prepared(dao, slots), checkpoint=lambda: None, team_results=request)
        saved, = dao.list_team_enhancements(team.team_id)
        assert {row["plan_id"] for row in saved.members} == set(ids)
        assert saved.payload["relative_gain"] == pytest.approx(.05)
        assert dao.completed_team_run(request.run_id, request.input_digest) == ids
        assert dao.integrity_check()["ok"]
        dao.validate_team_enhancement_data()


@pytest.mark.parametrize("failure", ["insert", "cancel", "partial", "source", "duplicate"])
def test_result_failure_cannot_leave_partial_plans_or_result(tmp_path, failure):
    service, team, slots = overview_case(tmp_path)
    with UserDataDao(service.database_path) as dao:
        request, rows = commit_for(dao, team), prepared(dao, slots)
        if failure == "source":
            dao.save_team_source(team, dao.get_team_source(team.team_id), mode=SourceMode.GAME)
        if failure == "duplicate":
            dao.save_calculated_loadout_plans(rows, checkpoint=lambda: None, team_results=request)
        if failure == "insert":
            dao._db().execute("CREATE TRIGGER fail_result BEFORE INSERT ON team_enhancement_result "
                              "BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
            dao._db().commit()
        if failure == "partial":
            rows = rows[:-1]
        before = tuple(dao._db().iterdump())
        def checkpoint():
            if failure == "cancel" and dao._db().execute("SELECT COUNT(*) FROM team_enhancement_result").fetchone()[0]:
                raise RuntimeError("cancelled before commit")
        with pytest.raises((ValueError, RuntimeError, sqlite3.IntegrityError)):
            dao.save_calculated_loadout_plans(rows, checkpoint=checkpoint, team_results=request)
        assert tuple(dao._db().iterdump()) == before


def test_joint_negative_result_and_deleting_one_team_preserves_other_and_plans(tmp_path):
    service, first, slots = overview_case(tmp_path)
    from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
    with static_catalog_read_unit(service.static_database_path) as unit:
        ids = tuple(row["character_id"] for row in unit.dao.list_role_template_characters()
                    if row["character_id"] not in first.character_ids and unit.dao.get_equipment_plan(row["character_id"]))[:4]
    _, second, second_slots = overview_case(tmp_path, ids)
    with UserDataDao(service.database_path) as dao:
        first_entry = commit_for(dao, first).entries[0]
        second_config = dao.get_team_source(second.team_id)
        value = payload(second, second_config, after=95)
        second_entry = TeamResultCommitEntry(second, second_config, tuple((str(key), key) for key in second.character_ids), validate_team_result_payload(value))
        joint = json.dumps(dict(version=1, team_ids=[first.team_id, second.team_id],
            normalized_baseline=1, normalized_candidate=1, relative_gain=0))
        request = replace(commit_for(dao, first), entries=(first_entry, second_entry), joint_summary_json=joint)
        rows = prepared(dao, slots) + prepared(dao, second_slots)
        ids = dao.save_calculated_loadout_plans(rows, checkpoint=lambda: None, team_results=request)
        other, = dao.list_team_enhancements(second.team_id)
        assert other.payload["relative_gain"] == pytest.approx(-.05)
        dao.delete_team(first)
        assert dao.list_team_enhancements(second.team_id) == (other,)
        assert dao.completed_team_run(request.run_id, request.input_digest) == ids
        assert len(dao.get_loadout_plans(ids)) == 8
        dao.delete_team(second)
        assert dao.completed_team_run(request.run_id, request.input_digest) is None
        assert len(dao.get_loadout_plans(ids)) == 8


def test_frozen_history_survives_member_change_and_missing_references(tmp_path):
    service, team, slots = overview_case(tmp_path)
    with UserDataDao(service.database_path) as dao:
        request = commit_for(dao, team)
        dao.save_calculated_loadout_plans(prepared(dao, slots), checkpoint=lambda: None, team_results=request)
        saved, = dao.list_team_enhancements(team.team_id)
        dao.deactivate_loadout_plan(saved.members[0]["plan_id"])
        dao.save_team(TeamDraft("改阵容", (1, 2, 3, 4)), existing=team)
        assert dao.list_team_enhancements(team.team_id) == (saved,)
        dao.validate_team_enhancement_data()


def test_result_format_rejects_raw_payload_nonfinite_and_forged_ratio(tmp_path):
    service, team, _ = overview_case(tmp_path)
    with UserDataDao(service.database_path) as dao:
        original = payload(team, dao.get_team_source(team.team_id))
    assert json.loads(validate_team_result_payload(original))["candidate_value"] == 105
    for changes in ({"relative_gain": 999}, {"candidate_value": float("nan")}, {"raw_hits": []},
                    {"coverage": {"coverage_ratio": 1}}, {"result_kind": "baseline", "relative_gain": 0}):
        with pytest.raises(ValueError):
            validate_team_result_payload(deepcopy(original) | changes)
    baseline = original | {"result_kind": "baseline", "baseline_value": None, "candidate_value": None, "relative_gain": None}
    assert json.loads(validate_team_result_payload(baseline))["relative_gain"] is None
