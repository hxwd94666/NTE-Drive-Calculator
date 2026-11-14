# 用隔离账号库验证目标追加版本、CAS、升级回滚、账号传输和删队伍边界；不操作实际账号库。
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from threading import Barrier

import pytest

from src.domain.teams.graduation.axis_graduation_target import decode_graduation_target
from src.domain.teams.models import TeamDraft
from src.services.teams.axis_graduation.inputs import TeamAxisCurrentInputService
from src.services.teams.axis_graduation.service import TeamAxisGraduationService
from src.storage.sqlite.accounts.transfer_database import backup_account_database, prepare_account_database
from src.storage.sqlite.user_data_dao import UserDataDao
from src.storage.sqlite.user_data_support import SCHEMA_VERSION


NTE_TEST_TIER = "core"
GOLDEN = Path(__file__).resolve().parents[2] / "fixtures/team_axis_graduation_target_v1.json"


def target(team, revision=1):
    return replace(decode_graduation_target(GOLDEN.read_text("utf8")), target_id="target-"+team.team_id,
                   team_id=team.team_id, revision=revision)


def case(path, account="synthetic"):
    with UserDataDao(path, account_id=account) as dao:
        team = dao.save_team(TeamDraft("合成队伍", (1, 2, 3, 4)))
    return team


def test_fresh_database_has_constraints_and_target_history_survives_reopen(tmp_path):
    path = tmp_path / "user.sqlite3"
    team = case(path)
    first, second = target(team), target(team, 2)
    with UserDataDao(path) as dao:
        assert dao._db().execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == SCHEMA_VERSION
        assert dao.get_team_axis_target(team.team_id) is None
        assert dao.save_team_axis_target(team, first, expected_revision=0) == first
        assert dao.save_team_axis_target(team, second, expected_revision=1) == second
        assert dao.get_team_axis_target(team.team_id, revision=1) == first
        assert dao.list_team_axis_targets(team.team_id) == (second, first)
        assert dao.list_team_axis_targets(team.team_id, offset=1, limit=1) == (first,)
        dao.validate_team_axis_targets()
    with UserDataDao(path) as dao:
        assert dao.get_team_axis_target(team.team_id) == second
        assert dao._db().execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("kind", ["stale", "jump", "identity", "members", "team"])
def test_revision_identity_or_team_conflict_never_overwrites_previous_version(tmp_path, kind):
    path = tmp_path / "user.sqlite3"
    team = case(path)
    with UserDataDao(path) as dao:
        first = dao.save_team_axis_target(team, target(team), expected_revision=0)
        proposed, expected = target(team, 2), 1
        if kind == "stale":
            proposed, expected = first, 0
        elif kind == "jump":
            proposed = target(team, 3)
        elif kind == "identity":
            proposed = replace(proposed, target_id="changed")
        elif kind == "members":
            team = replace(team, character_ids=(2, 1, 3, 4))
        else:
            dao.save_team(TeamDraft("改名", team.character_ids), existing=team)
        with pytest.raises(ValueError):
            dao.save_team_axis_target(team, proposed, expected_revision=expected)
        assert dao.list_team_axis_targets(first.team_id) == (first,)


def test_cancel_before_commit_and_outer_transaction_failure_leave_no_partial_target(tmp_path):
    path = tmp_path / "user.sqlite3"
    team = case(path)
    with UserDataDao(path) as dao:
        count = 0
        def cancel():
            nonlocal count
            count += 1
            if count == 2:
                raise CancelledError
        with pytest.raises(CancelledError):
            dao.save_team_axis_target(team, target(team), expected_revision=0, checkpoint=cancel)
        assert dao.list_team_axis_targets(team.team_id) == ()
        with pytest.raises(RuntimeError):
            with dao.write_transaction():
                dao.save_team_axis_target(team, target(team), expected_revision=0)
                raise RuntimeError("SYNTHETIC_OUTER_FAILURE")
        assert dao.list_team_axis_targets(team.team_id) == ()


def test_two_writers_have_one_winner_for_same_expected_revision(tmp_path):
    path = tmp_path / "user.sqlite3"
    team = case(path)
    barrier = Barrier(2)
    def write():
        with UserDataDao(path) as dao:
            barrier.wait(timeout=5)
            try:
                dao.save_team_axis_target(team, target(team), expected_revision=0)
                return "saved"
            except ValueError:
                return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _n: write(), range(2)))
    assert sorted(results) == ["conflict", "saved"]
    with UserDataDao(path) as dao:
        assert dao.list_team_axis_targets(team.team_id) == (target(team),)


def test_delete_team_cascades_only_its_targets_and_keeps_other_team_and_role_data(tmp_path):
    path = tmp_path / "user.sqlite3"
    team = case(path)
    with UserDataDao(path) as dao:
        other = dao.save_team(TeamDraft("另一队", team.character_ids))
        dao.save_team_axis_target(team, target(team), expected_revision=0)
        dao.save_team_axis_target(other, target(other), expected_revision=0)
        slot = dao.create_loadout_slot(1, "保留槽位")
        dao.delete_team(team)
        assert dao.list_team_axis_targets(team.team_id) == ()
        assert dao.get_team_axis_target(other.team_id) == target(other)
        assert dao.get_loadout_slot(slot) is not None and dao.list_teams() == (other,)


def test_separate_accounts_do_not_share_targets_even_with_same_team_ids(tmp_path):
    one, two = tmp_path / "one.sqlite3", tmp_path / "two.sqlite3"
    team = case(one, "one")
    with UserDataDao(one) as dao:
        dao.save_team_axis_target(team, target(team), expected_revision=0)
    with UserDataDao(two, account_id="two") as dao:
        assert dao.get_team_axis_target(team.team_id) is None
        with pytest.raises(ValueError):
            dao.save_team_axis_target(team, target(team), expected_revision=0)


def test_v47_upgrade_failure_rolls_back_ddl_and_version_then_retry_succeeds(tmp_path, monkeypatch):
    from src.storage.sqlite import user_data_base, user_data_support
    path = tmp_path / "legacy.sqlite3"
    with monkeypatch.context() as old:
        old.setattr(user_data_base, "SCHEMA_VERSION", 47)
        team = case(path)
    broken = tmp_path / "broken.sql"
    broken.write_text(user_data_support.USER_MIGRATIONS[48].read_text("utf8") + "\nSELECT * FROM synthetic_missing;", encoding="utf8")
    with monkeypatch.context() as failure:
        failure.setitem(user_data_base.USER_MIGRATIONS, 48, broken)
        with pytest.raises(RuntimeError):
            UserDataDao(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 47
        assert db.execute("SELECT name FROM sqlite_master WHERE name='team_axis_graduation_target'").fetchone() is None
    backups = list((tmp_path / ".migration-backups").glob("user-data-v47-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert db.execute("SELECT team_id FROM team").fetchone()[0] == team.team_id
    with UserDataDao(path) as dao:
        assert dao.list_teams() == (team,)
        dao.save_team_axis_target(team, target(team), expected_revision=0)


def test_backup_failure_prevents_migration_and_keeps_v47_retryable(tmp_path, monkeypatch):
    from src.storage.sqlite import user_data_base
    path = tmp_path / "legacy.sqlite3"
    with monkeypatch.context() as old:
        old.setattr(user_data_base, "SCHEMA_VERSION", 47)
        case(path)
    def fail(*_args):
        raise RuntimeError("SYNTHETIC_BACKUP_FAILURE")
    with monkeypatch.context() as failure:
        failure.setattr(user_data_base, "backup_before_user_migration", fail)
        with pytest.raises(RuntimeError):
            UserDataDao(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 47
    with UserDataDao(path) as dao:
        assert dao.list_teams()


@pytest.mark.parametrize("kind", ["digest", "gap", "unknown_field"])
def test_transfer_rejects_tampered_content_or_broken_history(kind, tmp_path):
    path = tmp_path / "input.sqlite3"
    team = case(path)
    with UserDataDao(path) as dao:
        dao.save_team_axis_target(team, target(team), expected_revision=0)
        if kind == "digest":
            dao._db().execute("UPDATE team_axis_graduation_target SET target_sha256=?", ("0"*64,))
        elif kind == "gap":
            dao.save_team_axis_target(team, target(team, 2), expected_revision=1)
            dao._db().execute("DELETE FROM team_axis_graduation_target WHERE revision=1")
        else:
            value = target(team).to_payload()
            value["unknown"] = "not accepted"
            dao._db().execute("UPDATE team_axis_graduation_target SET payload_json=?", (json.dumps(value),))
        dao._db().commit()
    with pytest.raises(ValueError):
        prepare_account_database(path, account_id="synthetic", account_name="synthetic")


def test_consistent_backup_and_transfer_keep_target_versions_without_touching_source(tmp_path):
    path, copy = tmp_path / "source.sqlite3", tmp_path / "candidate.sqlite3"
    team = case(path)
    with UserDataDao(path) as dao:
        first = dao.save_team_axis_target(team, target(team), expected_revision=0)
        second = dao.save_team_axis_target(team, target(team, 2), expected_revision=1)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    backup_account_database(path, copy)
    prepare_account_database(copy, account_id="synthetic", account_name="synthetic")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with UserDataDao(copy) as dao:
        assert dao.list_team_axis_targets(team.team_id) == (second, first)


def test_application_target_save_is_explicit_offline_and_uses_account_owner(tmp_path):
    path = tmp_path / "user.sqlite3"
    team = case(path)
    active = True
    inputs = TeamAxisCurrentInputService(account_id="synthetic", generation=1, database_path=path,
        static_database_path=None, context_is_current=lambda: active)
    def forbidden(**_options):
        raise AssertionError("target save must not start analysis")
    service = TeamAxisGraduationService(current_inputs=inputs, semantics_path=tmp_path / "unused.json", client_factory=forbidden)
    assert service.save_target(team, target(team), expected_revision=0) == target(team)
    assert service.list_targets(team) == (target(team),)
    active = False
    with pytest.raises(CancelledError):
        service.save_target(team, target(team, 2), expected_revision=1)
    wrong = TeamAxisCurrentInputService(account_id="other", generation=1, database_path=path,
        static_database_path=None, context_is_current=lambda: True)
    foreign = TeamAxisGraduationService(current_inputs=wrong, semantics_path=tmp_path / "unused.json", client_factory=forbidden)
    with pytest.raises(CancelledError):
        foreign.list_targets(team)


@pytest.mark.parametrize("payload", ["not-json", "{}", '{"format":"team_axis_graduation_target_v1","target_id":"wrong","team_id":"wrong","revision":1}'])
def test_database_checks_reject_malformed_or_conflicting_identity(payload, tmp_path):
    path = tmp_path / "user.sqlite3"
    team = case(path)
    with UserDataDao(path) as dao:
        first = dao.save_team_axis_target(team, target(team), expected_revision=0)
        with pytest.raises(sqlite3.IntegrityError):
            with dao.write_transaction() as db:
                db.execute("UPDATE team_axis_graduation_target SET payload_json=?", (payload,))
        assert dao.get_team_axis_target(team.team_id) == first


def test_actual_account_archive_roundtrip_keeps_targets(tmp_path):
    from src.features.accounts.manager import AccountManager
    from src.services.accounts.transfer import export_account_data, import_account_data
    manager = AccountManager(tmp_path / "runtime", tmp_path / "empty-bundle", lambda _p: [], (), ())
    manager.initialize()
    account = manager.create_account("合成账号")
    database = manager.account_dir(account) / "user_data.sqlite3"
    with UserDataDao(database) as dao:
        team = dao.save_team(TeamDraft("归档队伍", (1, 2, 3, 4)))
        first = dao.save_team_axis_target(team, target(team), expected_revision=0)
        second = dao.save_team_axis_target(team, target(team, 2), expected_revision=1)
    archive = export_account_data(manager, account, tmp_path / "synthetic-account.zip")
    with UserDataDao(database) as dao:
        dao.delete_team(team)
        assert dao.list_team_axis_targets(team.team_id) == ()
    assert import_account_data(manager, archive) == account
    with UserDataDao(database) as dao:
        assert dao.list_team_axis_targets(team.team_id) == (second, first)



def test_existing_analysis_component_reads_upgraded_account_without_target_invalidation(tmp_path):
    from src.integrations.analysis_core.analysis_core_release import create_bundled_analysis_client
    path = os.environ.get("NTE_TEAM_AXIS_GRADUATION_FILE_FIXTURE")
    if not path:
        pytest.skip("explicit existing controlled file fixture required")
    fixture = json.loads(Path(path).read_text("utf8"))
    original = Path(fixture["selector"]["user_database_path"])
    before_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    selector = dict(fixture["selector"])
    client = create_bundled_analysis_client(static_database_path=Path(selector["static_database_path"]))
    assert client is not None and client.supports_battle_page_identity
    before = client.load_battle_page_identity(selector)
    copy = tmp_path / "upgraded-account.sqlite3"
    backup_account_database(original, copy)
    with UserDataDao(copy) as dao:
        assert dao._db().execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == SCHEMA_VERSION
        extra = dao.save_team(TeamDraft("独立目标队伍", (1, 2, 3, 4)))
        dao.save_team_axis_target(extra, target(extra), expected_revision=0)
    selector["user_database_path"] = str(copy)
    after = client.load_battle_page_identity(selector)
    assert after == before
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before_hash
