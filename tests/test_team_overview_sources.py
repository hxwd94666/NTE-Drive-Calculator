# 验证队伍简报来源按账号及稳定槽位保存，迁移、并发和成员变化不覆盖旧事实。
from dataclasses import replace
import sqlite3

import pytest

from src.domain.teams.models import TeamDraft
from src.domain.teams.overview import SourceMode
from src.storage.sqlite.user_data_dao import UserDataDao


# 来源、事务与账号失效属于核心边界，显式分层避免仅按文件名遗漏。
NTE_TEST_TIER = "core"


def test_new_and_existing_teams_have_explicit_account_default(tmp_path):
    with UserDataDao(tmp_path / "one.sqlite3", account_id="one") as dao:
        team = dao.save_team(TeamDraft("队", (1, 2, 3, 4)))
        config = dao.get_team_source(team.team_id)
        assert config.mode == SourceMode.CALC and config.revision == 1 and config.bindings == ()
    with UserDataDao(tmp_path / "two.sqlite3", account_id="two") as dao:
        with pytest.raises(ValueError, match="队伍"):
            dao.get_team_source(team.team_id)


def test_bindings_use_slot_identity_and_validate_whole_batch(tmp_path):
    with UserDataDao(tmp_path / "user.sqlite3", account_id="one") as dao:
        team = dao.save_team(TeamDraft("队", (1, 2)))
        slots = [dao.create_loadout_slot(key, "同名") for key in (1, 2, 3)]
        config = dao.get_team_source(team.team_id)
        saved = dao.save_team_source(team, config, mode=SourceMode.CALC, bindings=((1, slots[0]), (2, slots[1])))
        assert saved.revision == 2
        with pytest.raises(ValueError, match="所属角色"):
            dao.save_team_source(team, saved, mode=SourceMode.CALC, bindings=((1, slots[0]), (2, slots[2])))
        assert dao.get_team_source(team.team_id) == saved
        with pytest.raises(ValueError, match="已变化"):
            dao.save_team_source(team, config, mode=SourceMode.GAME)


def test_rename_retains_binding_member_change_does_not_retarget(tmp_path):
    with UserDataDao(tmp_path / "user.sqlite3", account_id="one") as dao:
        team = dao.save_team(TeamDraft("旧名", (1, 2)))
        slot = dao.create_loadout_slot(1, "备用")
        config = dao.save_team_source(team, dao.get_team_source(team.team_id), mode=SourceMode.CALC, bindings=((1, slot),))
        renamed = dao.save_team(TeamDraft("新名", (2, 1)), existing=team)
        assert dao.get_team_source(team.team_id) == config
        with pytest.raises(ValueError, match="队伍"):
            dao.save_team_source(team, config, mode=SourceMode.GAME)
        changed = dao.save_team(TeamDraft("新名", (2, 3)), existing=renamed)
        assert dao.get_team_source(changed.team_id) == replace(config, bindings=(), revision=config.revision + 1)
        dao.delete_team(changed)
        assert dao.get_loadout_slot(slot) is not None


def test_source_save_cancel_rolls_back(tmp_path):
    with UserDataDao(tmp_path / "user.sqlite3", account_id="one") as dao:
        team = dao.save_team(TeamDraft("队", (1,)))
        config = dao.get_team_source(team.team_id)
        def cancelled():
            raise RuntimeError("cancelled")
        with pytest.raises(RuntimeError, match="cancelled"):
            dao.save_team_source(team, config, mode=SourceMode.GAME, checkpoint=cancelled)
        assert dao.get_team_source(team.team_id) == config


def test_v46_upgrade_failure_rollback_backup_and_retry(tmp_path, monkeypatch):
    from src.storage.sqlite import user_data_base, user_data_support
    path = tmp_path / "old.sqlite3"
    with monkeypatch.context() as patch:
        patch.setattr(user_data_base, "SCHEMA_VERSION", 46)
        with UserDataDao(path, account_id="one") as dao:
            # 旧库夹具按旧 schema 写入，不用新版 DAO 方法反向操作旧结构。
            dao._db().execute("INSERT INTO team VALUES('legacy','旧队',1,'2026-10-22','2026-10-22')")
            dao._db().executemany("INSERT INTO team_member VALUES('legacy',?,?)", [(0, 1), (1, 2)])
            dao._db().commit()
            team, = dao.list_teams()
    broken = tmp_path / "broken.sql"
    broken.write_text(user_data_support.USER_MIGRATIONS[47].read_text(encoding="utf-8") +
                      "\nINSERT INTO missing_table VALUES(1);", encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setitem(user_data_base.USER_MIGRATIONS, 47, broken)
        with pytest.raises(RuntimeError, match="升级"):
            UserDataDao(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 46
        assert db.execute("SELECT name FROM sqlite_master WHERE name='team_overview_source'").fetchone() is None
    assert list((tmp_path / ".migration-backups").glob("user-data-v46-*.sqlite3"))
    with UserDataDao(path) as dao:
        assert dao.list_teams() == (team,)
        assert dao.get_team_source(team.team_id).mode == SourceMode.CALC


def test_data_downgrade_preserves_new_database_then_restores_complete_old_backup(tmp_path, monkeypatch):
    from shutil import copyfile
    from src.storage.sqlite import user_data_base
    from src.storage.sqlite.user_data_migration_backup import backup_before_user_migration
    path = tmp_path / "upgrade.sqlite3"
    with monkeypatch.context() as patch:
        patch.setattr(user_data_base, "SCHEMA_VERSION", 46)
        with UserDataDao(path, account_id="one") as dao:
            dao._db().execute("INSERT INTO team VALUES('old','旧队',1,'2026-10-22','2026-10-22')")
            dao._db().commit()
    with UserDataDao(path) as dao:
        team, = dao.list_teams()
        dao.save_team_source(team, dao.get_team_source(team.team_id), mode=SourceMode.GAME)
    old_backup, = (tmp_path / ".migration-backups").glob("user-data-v46-*.sqlite3")
    preserved = backup_before_user_migration(path, user_data_base.SCHEMA_VERSION)
    recovery = tmp_path / "restored-v46.sqlite3"
    copyfile(old_backup, recovery)  # 关闭的一致性备份，不复制活动库/WAL。
    with monkeypatch.context() as patch:
        patch.setattr(user_data_base, "SCHEMA_VERSION", 46)
        with pytest.raises(RuntimeError, match="高于当前"):
            UserDataDao(path)
        with UserDataDao(recovery) as dao:
            assert dao.list_teams() == (team,)
    with UserDataDao(preserved) as dao:
        assert dao.get_team_source(team.team_id).mode == SourceMode.GAME
