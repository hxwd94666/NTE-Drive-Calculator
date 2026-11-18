# 验证两种来源、只读草稿、稳定槽位及旧自动来源升级；迁移失败保留旧库并允许重试。
from concurrent.futures import CancelledError
import sqlite3

import pytest

from src.domain.teams.models import TeamDraft
from src.domain.teams.overview import SourceMode
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.teams.overview.team_overview_fixture import overview_case

NTE_TEST_TIER = "core"


def test_only_selected_calculation_and_game_sources_remain(tmp_path):
    assert {mode.value for mode in SourceMode} == {"slots", "game"}
    with pytest.raises(ValueError):
        SourceMode("current")
    with UserDataDao(tmp_path / "new.sqlite3", account_id="new") as dao:
        team = dao.save_team(TeamDraft("队", (1,)))
        assert dao.get_team_source(team.team_id).mode == SourceMode.CALC
        assert not dao.get_team_source(team.team_id).bindings
        with pytest.raises(sqlite3.IntegrityError):
            dao._db().execute("UPDATE team_overview_source SET mode='current' WHERE team_id=?", (team.team_id,))


def test_legacy_history_source_is_readable_but_not_reintroduced_as_a_live_mode(tmp_path):
    from src.domain.teams.enhancement import validate_team_result_payload
    from tests.teams.enhancement.test_team_enhancement_persistence import payload

    service, team, _slots = overview_case(tmp_path)
    config, _ = service.source_choices(team)
    old = payload(team, config)
    old.update(source_mode="current", source_bindings=[])
    frozen = dict(old)
    validate_team_result_payload(old)
    assert old == frozen and old["source_mode"] == "current"
    with pytest.raises(ValueError):
        service.save_source(team, config, mode="current")


def test_preview_consumes_selected_slots_without_writing_or_falling_back(tmp_path):
    service, team, slots = overview_case(tmp_path)
    # 多槽位必须显式选择；唯一已保存槽位的默认选择另有公共用例覆盖。
    with UserDataDao(service.database_path) as dao:
        dao.create_loadout_slot(team.character_ids[0], '备用')
    config, _ = service.source_choices(team)
    bindings = tuple((key, slot) for key, slot in slots.items() if key != team.character_ids[0])
    with UserDataDao(service.database_path) as dao:
        before = tuple(dao._db().iterdump())
    preview = service.preview_source(team, config, mode=SourceMode.CALC, bindings=bindings)
    assert preview.ready_count == 3 and preview.members[0].slot_id is None
    assert not preview.members[0].items and preview.members[0].issues
    assert service.preview_source(team, config, mode=SourceMode.GAME).ready_count == 4
    with UserDataDao(service.database_path) as dao:
        assert tuple(dao._db().iterdump()) == before
        assert dao.get_team_source(team.team_id) == config
    with pytest.raises(CancelledError):
        service.preview_source(team, config, mode=SourceMode.GAME, cancelled=lambda: True)
    service.save_source(team, config, mode=SourceMode.GAME)
    with pytest.raises(ValueError, match="来源已变化"):
        service.preview_source(team, config, mode=SourceMode.CALC, bindings=bindings)


def legacy(path, monkeypatch):
    from src.storage.sqlite import user_data_base

    with monkeypatch.context() as patch:
        patch.setattr(user_data_base, "SCHEMA_VERSION", 50)
        with UserDataDao(path, account_id="legacy") as dao:
            teams = [dao.save_team(TeamDraft(name, (1, 2))) for name in ("自动", "所选", "游戏")]
            primary = dao.create_loadout_slot(1, "主力", slot_key="primary")
            backup = dao.create_loadout_slot(1, "备用")
            dao.save_team_source(
                teams[1], dao.get_team_source(teams[1].team_id), mode=SourceMode.CALC, bindings=((1, backup),)
            )
            dao.save_team_source(teams[2], dao.get_team_source(teams[2].team_id), mode=SourceMode.GAME)
            dao._db().execute(
                "UPDATE team_overview_source SET mode='current',revision=3 WHERE team_id=?", (teams[0].team_id,)
            )
            dao._db().commit()
    return teams, primary, backup


def test_v50_upgrade_freezes_original_choice_and_preserves_explicit_modes(tmp_path, monkeypatch):
    path = tmp_path / "legacy.sqlite3"
    teams, primary, backup = legacy(path, monkeypatch)
    with UserDataDao(path) as dao:
        first, specified, game = [dao.get_team_source(team.team_id) for team in teams]
        assert first.mode == SourceMode.CALC and first.bindings == ((1, primary),) and first.revision == 4
        assert specified.mode == SourceMode.CALC and specified.bindings == ((1, backup),) and specified.revision == 2
        assert game.mode == SourceMode.GAME and game.bindings == () and game.revision == 2
        # 成员2无槽位继续缺项；升级只冻结一次，此后新增槽位不自动改绑。
        dao.create_loadout_slot(2, "后建主力", slot_key="primary")
        assert dao.get_team_source(teams[0].team_id) == first
        # 同一毫秒内创建的队伍按随机 team_id 排序；这里只核对升级未改动队伍本身。
        assert sorted(dao.list_teams(), key=lambda team: team.team_id) == sorted(teams, key=lambda team: team.team_id)
        assert dao._db().execute("PRAGMA foreign_key_check").fetchall() == []
        assert dao._db().execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert list((tmp_path / ".migration-backups").glob("user-data-v50-*.sqlite3"))


def test_v51_migration_failure_rolls_back_tables_and_retry_preserves_bindings(tmp_path, monkeypatch):
    from src.storage.sqlite import user_data_base

    path = tmp_path / "legacy.sqlite3"
    teams, primary, backup = legacy(path, monkeypatch)
    with sqlite3.connect(path) as db:
        before = tuple(db.iterdump())
    broken = tmp_path / "broken.sql"
    broken.write_text(
        user_data_base.USER_MIGRATIONS[51].read_text(encoding="utf8") + "\nSELECT * FROM synthetic_missing_table;",
        encoding="utf8",
    )
    with monkeypatch.context() as patch:
        patch.setitem(user_data_base.USER_MIGRATIONS, 51, broken)
        with pytest.raises(RuntimeError, match="升级"):
            UserDataDao(path)
    with sqlite3.connect(path) as db:
        assert tuple(db.iterdump()) == before
        assert db.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 50
    with UserDataDao(path) as dao:
        assert dao.get_team_source(teams[0].team_id).bindings == ((1, primary),)
        assert dao.get_team_source(teams[1].team_id).bindings == ((1, backup),)
