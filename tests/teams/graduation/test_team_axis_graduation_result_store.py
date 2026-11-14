# 验证数学结果的原子保存、显式选择、账号隔离、可迁移正文及升级回滚，不假称真实伤害或UI验收。
from concurrent.futures import CancelledError, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
from threading import Barrier
import zipfile

import pytest

from src.domain.teams.graduation.axis_graduation_record import MAX_RECORD_BYTES, SavedAxisResult
from src.domain.teams.graduation.axis_graduation_target import decode_graduation_target
from src.domain.teams.models import TeamDraft
from src.domain.teams.report_matching import ReportRange
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError
from src.integrations.battle_analysis.graduation.team_axis_graduation_archive import archive_axis_result, validate_saved_axis_result
from src.services.teams.axis_graduation.service import _request_digest
from src.storage.sqlite.user_data_dao import UserDataDao
from src.storage.sqlite.user_data_support import SCHEMA_VERSION
from tests.teams.graduation.test_team_axis_graduation_current import current_response
from tests.teams.graduation.test_team_axis_graduation_service import fixture as service_fixture
from tests.teams.graduation.test_team_axis_graduation_service import solve
from tests.teams.graduation.test_team_axis_graduation_wire import response_fixture
from tests.teams.graduation.test_team_axis_graduation_wire import wire_fixture


NTE_TEST_TIER = "core"


def records(path, account="synthetic-current"):
    request = wire_fixture()
    with UserDataDao(path, account_id=account) as dao:
        team = dao.save_team(TeamDraft("合成队伍", tuple(request["current"]["character_ids"])))
    request["battle_request"]["account_id"] = request["current"]["account_id"] = account
    request["current"]["team_id"] = request["target"]["team_id"] = team.team_id
    request["target"]["target_id"] = team.team_id + "-target"
    request["target_sha256"] = decode_graduation_target(json.dumps(request["target"])).sha256
    response = response_fixture(request, _request_digest(request, "fixture"))
    benchmark = archive_axis_result(request, response)
    current = deepcopy(request)
    current.pop("budget")
    current.pop("seeds")
    current.update(operation="current_evaluation", reference=response["benchmark_reference"], axis_snapshot=response["axis_snapshot"])
    current["current"]["source_sha256"] = "9"*64
    current["request_id"] = "synthetic-updated-current"
    updated = archive_axis_result(current, current_response(current, response, _request_digest(current, "fixture")))
    return team, benchmark, updated


def save(dao, team, result, target=0, selected=0, **options):
    return dao.save_team_axis_result(team, result, expected_target_revision=target,
                                    expected_selection_revision=selected, **options)


def test_fresh_store_reopens_complete_baseline_current_and_explicit_selection(tmp_path):
    path = tmp_path / "user.sqlite3"
    team, baseline, current = records(path)
    with UserDataDao(path) as dao:
        assert dao._db().execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == SCHEMA_VERSION
        save(dao, team, baseline)
        first = dao.get_team_axis_selection(team.team_id)
        save(dao, team, current, target=1, selected=1, select=False)
        assert dao.get_team_axis_selection(team.team_id) == first
        assert dao.get_team_axis_target(team.team_id) == baseline.target
        assert dao.get_team_axis_evaluation(team.team_id, baseline.evaluation_id) == baseline
        assert dao._db().execute("SELECT payload_json FROM team_axis_graduation_evaluation WHERE operation='theoretical_benchmark'").fetchone()[0] is None
    with UserDataDao(path) as dao:
        assert dao.list_team_axis_benchmarks(team.team_id) == (baseline,)
        assert dao.list_team_axis_evaluations(team.team_id, baseline.benchmark_id) == (current, baseline)
        chosen = dao.select_team_axis_result(team, current.evaluation_id, expected_revision=1)
        assert chosen.revision == 2 and chosen.benchmark_id == baseline.benchmark_id
        assert dao.get_team_axis_evaluation(team.team_id, chosen.evaluation_id).reference == baseline.reference
        assert dao._db().execute("PRAGMA foreign_key_check").fetchall() == []


def test_portable_archive_is_detached_bounded_and_contains_no_selector_paths_or_generation(tmp_path):
    _, baseline, current = records(tmp_path / "user.sqlite3")
    for result in (baseline, current):
        value = result.to_payload()
        assert set(value["request"]["battle_request"]) == {"account_id", "battle_record_id", "detail_level"}
        assert "generation" not in value["request"]["current"]
        assert "F:/synthetic/" not in result.body_json
        assert "reference" not in value["request"] and "axis_snapshot" not in value["request"]
        assert validate_saved_axis_result(result) == result
        value["response"]["current_ratio_percent"] = 70
        with pytest.raises(ValueError):
            SavedAxisResult(json.dumps(value))
    with pytest.raises(ValueError):
        SavedAxisResult(" "*(MAX_RECORD_BYTES+1))
    with pytest.raises(ValueError):
        SavedAxisResult(baseline.body_json.replace('"format":', '"format":"other","format":', 1))


def test_cancel_after_inserts_and_outer_failure_leave_no_target_baseline_evaluation_or_pointer(tmp_path):
    path = tmp_path / "user.sqlite3"
    team, baseline, _ = records(path)
    saw_uncommitted_selection = False
    def cancel():
        nonlocal saw_uncommitted_selection
        if dao.get_team_axis_selection(team.team_id) is not None:
            saw_uncommitted_selection = True
            raise CancelledError
    with UserDataDao(path) as dao:
        with pytest.raises(CancelledError):
            save(dao, team, baseline, checkpoint=cancel)
        assert saw_uncommitted_selection  # 指针已插入但尚未提交，撤权必须回滚整套事实。
        for table in ("target", "benchmark", "evaluation", "selection"):
            assert dao._db().execute("SELECT count(*) FROM team_axis_graduation_"+table).fetchone()[0] == 0
        with pytest.raises(RuntimeError):
            with dao.write_transaction():
                save(dao, team, baseline)
                raise RuntimeError("SYNTHETIC_OUTER_FAILURE")
        assert dao.get_team_axis_selection(team.team_id) is None
        assert dao.get_team_axis_target(team.team_id) is None
        save(dao, team, baseline)


def test_current_requires_saved_denominator_and_cannot_replace_target_or_reference(tmp_path):
    path = tmp_path / "user.sqlite3"
    team, baseline, current = records(path)
    with UserDataDao(path) as dao:
        with pytest.raises(ValueError):
            save(dao, team, current)
        save(dao, team, baseline)
        dao.save_team_axis_target(team, replace(baseline.target, revision=2), expected_revision=1)
        with pytest.raises(ValueError):
            save(dao, team, current, target=1, selected=1)
        with pytest.raises(ValueError):
            save(dao, team, current, target=2, selected=1)
        assert dao.list_team_axis_evaluations(team.team_id, baseline.benchmark_id) == (baseline,)


def test_same_content_save_is_idempotent_but_stale_selection_and_target_cas_are_rejected(tmp_path):
    path = tmp_path / "user.sqlite3"
    team, baseline, current = records(path)
    with UserDataDao(path) as dao:
        save(dao, team, baseline)
        save(dao, team, baseline, target=1, selected=1)
        assert len(dao.list_team_axis_benchmarks(team.team_id)) == 1
        assert len(dao.list_team_axis_evaluations(team.team_id, baseline.benchmark_id)) == 1
        with pytest.raises(ValueError):
            save(dao, team, current, target=1, selected=1)
        with pytest.raises(ValueError):
            save(dao, team, current, target=0, selected=2)
        assert dao.get_team_axis_selection(team.team_id).revision == 2


def test_new_search_appends_a_new_denominator_but_never_silently_selects_it(tmp_path):
    path = tmp_path / "user.sqlite3"
    team, baseline, _ = records(path)
    request = baseline.to_payload()["request"]
    request["current"]["generation"] = 1
    request["battle_request"].update(generation=1, user_database_path="F:/synthetic/user.sqlite3",
        static_database_path="F:/synthetic/static.sqlite3", semantics_path="F:/synthetic/semantics.json")
    request["request_id"] = "synthetic-explicit-new-search"
    newer = archive_axis_result(request, response_fixture(request, _request_digest(request, "fixture")))
    assert newer.benchmark_id != baseline.benchmark_id
    with UserDataDao(path) as dao:
        save(dao, team, baseline)
        old = dao.get_team_axis_selection(team.team_id)
        save(dao, team, newer, target=1, selected=1, select=False)
        assert dao.get_team_axis_selection(team.team_id) == old
        assert dao.list_team_axis_benchmarks(team.team_id) == (newer, baseline)
        assert dao.get_team_axis_evaluation(team.team_id, old.evaluation_id).reference == baseline.reference


def test_concurrent_selection_has_one_winner_and_does_not_duplicate_history(tmp_path):
    path = tmp_path / "user.sqlite3"
    team, baseline, current = records(path)
    with UserDataDao(path) as dao:
        save(dao, team, baseline)
    barrier = Barrier(2)
    def write(_n):
        with UserDataDao(path) as dao:
            barrier.wait(timeout=5)
            try:
                save(dao, team, current, target=1, selected=1)
                return "saved"
            except ValueError:
                return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, range(2))) == ["conflict", "saved"]
    with UserDataDao(path) as dao:
        assert dao.get_team_axis_selection(team.team_id).revision == 2
        assert dao.list_team_axis_evaluations(team.team_id, baseline.benchmark_id) == (current, baseline)


def test_account_team_isolation_and_delete_cascade_keep_unrelated_role_data(tmp_path):
    path = tmp_path / "one.sqlite3"
    team, baseline, current = records(path)
    with UserDataDao(path) as dao:
        save(dao, team, baseline)
        save(dao, team, current, target=1, selected=1)
        other = dao.save_team(TeamDraft("保留队伍", team.character_ids))
        slot = dao.create_loadout_slot(1, "保留槽位")
        with pytest.raises(ValueError):
            dao.select_team_axis_result(other, current.evaluation_id, expected_revision=0)
        dao.delete_team(team)
        for table in ("target", "benchmark", "evaluation", "selection"):
            assert dao._db().execute("SELECT count(*) FROM team_axis_graduation_"+table).fetchone()[0] == 0
        assert dao.get_loadout_slot(slot) is not None and dao.list_teams() == (other,)
    with UserDataDao(tmp_path / "two.sqlite3", account_id="other") as dao:
        with pytest.raises(ValueError):
            save(dao, team, baseline)


def test_v48_upgrade_failure_backup_rollback_and_retry(tmp_path, monkeypatch):
    from src.storage.sqlite import user_data_base
    path = tmp_path / "legacy.sqlite3"
    with monkeypatch.context() as old:
        old.setattr(user_data_base, "SCHEMA_VERSION", 48)
        team, baseline, _ = records(path)
        with UserDataDao(path) as dao:
            dao.save_team_axis_target(team, baseline.target, expected_revision=0)
    broken = tmp_path / "broken.sql"
    broken.write_text(user_data_base.USER_MIGRATIONS[49].read_text("utf8") + "\nSELECT * FROM synthetic_missing;", encoding="utf8")
    with monkeypatch.context() as failure:
        failure.setitem(user_data_base.USER_MIGRATIONS, 49, broken)
        with pytest.raises(RuntimeError):
            UserDataDao(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 48
        assert db.execute("SELECT name FROM sqlite_schema WHERE name='team_axis_graduation_benchmark'").fetchone() is None
    backups = list((tmp_path / ".migration-backups").glob("user-data-v48-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert db.execute("SELECT team_id FROM team_axis_graduation_target").fetchone()[0] == team.team_id
    with UserDataDao(path) as dao:
        save(dao, team, baseline, target=1)


def test_account_roundtrip_carries_results_and_selection_without_local_selector_paths(tmp_path):
    from src.features.accounts.manager import AccountManager
    from src.services.accounts.transfer import export_account_data, import_account_data
    manager = AccountManager(tmp_path / "runtime", tmp_path / "empty-bundle", lambda _p: [], (), ())
    manager.initialize()
    account = manager.create_account("合成账号")
    database = manager.account_dir(account) / "user_data.sqlite3"
    team, baseline, current = records(database, account)
    with UserDataDao(database) as dao:
        save(dao, team, baseline)
        save(dao, team, current, target=1, selected=1)
        selected = dao.get_team_axis_selection(team.team_id)
    archive = export_account_data(manager, account, tmp_path / "synthetic.zip")
    with UserDataDao(database) as dao:
        dao.delete_team(team)
    assert import_account_data(manager, archive) == account
    with UserDataDao(database) as dao:
        assert dao.get_team_axis_selection(team.team_id) == selected
        assert dao.get_team_axis_evaluation(team.team_id, selected.evaluation_id) == current
        assert dao.get_team_axis_benchmark(team.team_id, selected.benchmark_id) == baseline


def test_protocol_corruption_with_resealed_record_is_rejected_by_account_export(tmp_path):
    from src.features.accounts.manager import AccountManager
    from src.services.accounts.transfer import export_account_data
    manager = AccountManager(tmp_path / "runtime", tmp_path / "empty-bundle", lambda _p: [], (), ())
    manager.initialize()
    account = manager.create_account("合成账号")
    path = manager.account_dir(account) / "user_data.sqlite3"
    team, baseline, _ = records(path, account)
    value = baseline.to_payload()
    value["response"]["current"]["axis_evaluation"]["quantified_events"] = 999
    forged = SavedAxisResult(json.dumps(value))  # 内容SHA不冒充完整协议或来源真实性校验。
    with UserDataDao(path) as dao:
        save(dao, team, forged)
    with pytest.raises(NativeAnalysisError):
        export_account_data(manager, account, tmp_path / "rejected.zip")
    assert not (tmp_path / "rejected.zip").exists()


def test_invalid_protocol_import_retains_live_account_index_and_never_releases_owner(tmp_path):
    from src.features.accounts.manager import AccountManager
    from src.services.accounts.transfer import import_account_data
    manager = AccountManager(tmp_path / "runtime", tmp_path / "empty-bundle", lambda _p: [], (), ())
    manager.initialize()
    account = manager.create_account("合成账号")
    live = manager.account_dir(account) / "user_data.sqlite3"
    team, baseline, _ = records(live, account)
    with UserDataDao(live) as dao:
        save(dao, team, baseline)
    candidate = tmp_path / "candidate.sqlite3"
    other_team, other, _ = records(candidate, account)
    body = other.to_payload()
    body["response"]["current"]["axis_evaluation"]["quantified_events"] = 999
    with UserDataDao(candidate) as dao:
        save(dao, other_team, SavedAxisResult(json.dumps(body)))
        dao._db().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    archive = tmp_path / "invalid-import.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": "nte-account-export", "version": 1,
                                               "account": {"id": account, "name": "合成账号"}}))
        z.write(candidate, "account/user_data.sqlite3")
    index = manager.read_index()
    released = []
    with pytest.raises(NativeAnalysisError):
        import_account_data(manager, archive, before_replace=released.append)
    assert released == [] and manager.read_index() == index
    with UserDataDao(live) as dao:
        assert dao.get_team_axis_evaluation(team.team_id, baseline.evaluation_id) == baseline


def test_controlled_native_advice_can_be_archived_without_losing_replay_or_gain_evidence():
    path = os.environ.get("NTE_TEAM_AXIS_GRADUATION_ADVICE_WIRE_RESULT")
    if not path:
        pytest.skip("explicit controlled native advice response required")
    value = json.loads(Path(path).read_text("utf8"))
    assert value["fixture_kind"] == "synthetic_graduation_advice_wire_v1"
    request = json.loads(value["request_bytes"])["request"]
    record = archive_axis_result(request, value["response"])
    restored = validate_saved_axis_result(SavedAxisResult(record.body_json)).to_payload()["response"]
    assert restored["recommendations"] == value["response"]["recommendations"]
    assert restored["axis_snapshot"] == value["response"]["axis_snapshot"]
    assert restored["reference_verification"]["search_evaluations"] == 0


def test_service_save_uses_source_guard_and_history_read_does_not_need_component(tmp_path):
    service, inputs, client, values, _ = service_fixture()
    path = tmp_path / "user.sqlite3"
    with UserDataDao(path, account_id=inputs.current.account_id) as dao:
        # 此处将数据库队伍正式身份用于已有纯编排夹具，不复制任何真实账号数据。
        team = dao.save_team(TeamDraft("合成队伍", inputs.current.team.character_ids))
    inputs.current = replace(inputs.current, team=team, source=replace(inputs.current.source, team_id=team.team_id))
    values["target"] = replace(values["target"], team_id=team.team_id)
    inputs.database_path, inputs.account_id = path, inputs.current.account_id
    preview = solve(service, inputs, values, ReportRange(1))
    saved = service.save_completed_result(preview, expected_target_revision=0, expected_selection_revision=0)
    def forbidden(**_kwargs):
        raise AssertionError("history read must not start analysis")
    original_factory = service._factory
    service._factory = forbidden
    selected, result = service.selected_result(team)
    assert result == saved and selected.evaluation_id == saved.evaluation_id
    assert service.list_benchmarks(team) == (saved,)
    assert service.list_evaluations(team, saved.benchmark_id) == (saved,)
    service._factory = original_factory
    inputs.current = replace(inputs.current, source=replace(inputs.current.source, revision=2))
    with pytest.raises(ValueError):
        service.save_completed_result(preview, expected_target_revision=1, expected_selection_revision=1)
    assert service.selected_result(team) == (selected, result)
    inputs.active = False
    with pytest.raises(CancelledError):
        service.selected_result(team)
