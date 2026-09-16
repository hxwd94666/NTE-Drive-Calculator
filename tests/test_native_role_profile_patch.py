# 以临时账号数据库验证角色稀疏同步、字段来源、事务回滚与升级重试。
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.services.official_role_profile_service import OfficialRoleProfileService
from src.services.native_role_profile_projection import project_native_role_profile
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError, UserDataError
from src.storage.sqlite import user_data_base


def defaults(ids):
    return {value: {"character_level": 80, "breakthrough_stage": 6} for value in ids}


def setup(tmp_path):
    database = tmp_path / "account.sqlite3"
    with UserDataDao(database, account_id="fixture"):
        pass
    return database, OfficialRoleProfileService(database, growth_defaults_loader=defaults)


def seed(dao, character_id=1003, **updates):
    fields = dict(character_id=character_id, character_level=60, breakthrough_stage=5, awakening_level=1,
                  selected_awaken_effect_ids=["awaken_fixture"], awakening_selection_initialized=True,
                  fork_id="fork_fixture", fork_level=80, fork_breakthrough_stage=6, fork_refinement_level=3,
                  skill_levels={"skill_fixture": 4}, selected_skill_id="skill_fixture", ordinal=7,
                  likeability_level_10_enabled=True, is_active=True)
    fields.update(updates)
    return dao.save_character_profile(**fields)


def test_first_sparse_observation_preserves_template_sources_without_full_profile(tmp_path):
    database, service = setup(tmp_path)
    assert service.patch_native_profiles([{"character_id": 1003, "character_level": 70}], check=lambda: None) == 1
    with UserDataDao(database) as dao:
        assert dao.get_character_profile(1003) is None
        observation = dao.get_native_character_profile_observation(1003)
        assert observation["character_level"] == 70 and observation["breakthrough_stage"] is None
    template = {"character_id": 1003, "character_level": 80, "breakthrough_stage": 6, "skill_levels": {"skill": 9}, "awakening_level": 0}
    projected = project_native_role_profile(template, observation, persisted=False)
    assert projected["character_level"] == 70 and projected["breakthrough_stage"] == 6
    assert projected["field_sources"]["character_level"] == "native_observed"
    assert projected["field_sources"]["breakthrough_stage"] == "template"
    assert projected["field_sources"]["skill_levels"] == "template"
    assert template["character_level"] == 80


def test_existing_role_only_changes_explicit_growth_fields_and_leaves_other_roles(tmp_path):
    database, service = setup(tmp_path)
    with UserDataDao(database) as dao:
        before = seed(dao)
        other = seed(dao, 1004)
    service.patch_native_profiles([{"character_id": 1003, "character_level": 61, "breakthrough_stage": None,
                                   "awakening_level": 6, "skill_levels": {}}], check=lambda: None)
    with UserDataDao(database) as dao:
        after = dao.get_character_profile(1003)
        assert after["character_level"] == 61
        for key in before.keys() - {"character_level", "updated_at_utc"}:
            assert after[key] == before[key]
        assert dao.get_character_profile(1004) == other
        assert dao.get_native_character_profile_observation(1004) is None


def test_null_missing_and_unobserved_roles_do_not_clear_prior_observations(tmp_path):
    database, service = setup(tmp_path)
    service.patch_native_profiles([{"character_id": 1003, "character_level": 70}], check=lambda: None)
    assert service.patch_native_profiles([{"character_id": 1003, "character_level": None}, {"character_id": 1004}], check=lambda: None) == 0
    with UserDataDao(database) as dao:
        assert dao.get_native_character_profile_observation(1003)["character_level"] == 70
        assert dao.get_native_character_profile_observation(1004) is None


def test_final_combination_conflict_rolls_back_whole_batch_without_guessing_stage(tmp_path):
    database, service = setup(tmp_path)
    with pytest.raises(UserDataValidationError, match="最终组合"):
        service.patch_native_profiles([{"character_id": 1003, "character_level": 70},
                                       {"character_id": 1004, "character_level": 10}], check=lambda: None)
    with UserDataDao(database) as dao:
        assert dao.get_native_character_profile_observation(1003) is None
        assert dao.get_native_character_profile_observation(1004) is None


@pytest.mark.parametrize("reason", ["cancel", "account_generation", "mode_revoked"])
def test_final_check_failure_rolls_back_existing_and_new_role_updates(tmp_path, reason):
    database, service = setup(tmp_path)
    with UserDataDao(database) as dao:
        before = seed(dao)
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        # Service initial/read check; DAO initial/two rows/final commit check.
        if calls == 6:
            raise PermissionError(reason)

    with pytest.raises(PermissionError, match=reason):
        service.patch_native_profiles([{"character_id": 1003, "character_level": 61},
                                       {"character_id": 1004, "character_level": 70}], check=check)
    with UserDataDao(database) as dao:
        assert dao.get_character_profile(1003) == before
        assert dao.get_native_character_profile_observation(1003) is None
        assert dao.get_native_character_profile_observation(1004) is None
    assert service.patch_native_profiles([{"character_id": 1004, "character_level": 70}], check=lambda: None) == 1


def test_manual_full_save_clears_observation_in_same_transaction(tmp_path):
    database, service = setup(tmp_path)
    service.patch_native_profiles([{"character_id": 1003, "character_level": 70}], check=lambda: None)
    with UserDataDao(database) as dao:
        seed(dao)
        assert dao.get_native_character_profile_observation(1003) is None
        assert dao.get_character_profile(1003)["character_level"] == 60


def test_single_and_all_reset_clear_sparse_only_roles(tmp_path):
    database, service = setup(tmp_path)
    service.patch_native_profiles([{"character_id": 1003, "character_level": 70},
                                   {"character_id": 1004, "breakthrough_stage": 6}], check=lambda: None)
    service.reset_profile(1003)
    with UserDataDao(database) as dao:
        assert dao.get_native_character_profile_observation(1003) is None
        assert dao.get_native_character_profile_observation(1004) is not None
    assert service.reset_all_profiles() == 1
    with UserDataDao(database) as dao:
        assert dao.get_native_character_profile_observation(1004) is None


@pytest.mark.parametrize("profiles", [[{"character_id": True, "character_level": 70}],
    [{"character_id": 1003, "character_level": 81}],
    [{"character_id": 1003, "character_level": 70}, {"character_id": 1003, "breakthrough_stage": 6}]])
def test_invalid_formal_fields_fail_without_partial_write(tmp_path, profiles):
    database, service = setup(tmp_path)
    with pytest.raises(UserDataValidationError):
        service.patch_native_profiles(profiles, check=lambda: None)
    with UserDataDao(database) as dao:
        assert dao.get_native_character_profile_observation(1003) is None


def test_new_database_and_v40_upgrade_preserve_existing_profiles(tmp_path):
    database, _service = setup(tmp_path)
    with UserDataDao(database) as dao:
        assert dao.summary()["schema_version"] == 42
        before = seed(dao)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE character_profile_observation")
        connection.execute("DELETE FROM schema_migration WHERE version >= 41")
    with UserDataDao(database) as dao:
        assert dao.summary()["schema_version"] == 42
        assert dao.get_character_profile(1003) == before
        assert dao.get_native_character_profile_observation(1003) is None


def test_v41_migration_failure_rolls_back_and_retries(tmp_path):
    database, _service = setup(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE character_profile_observation")
        connection.execute("DELETE FROM schema_migration WHERE version >= 41")
    migration = Path(user_data_base.USER_MIGRATIONS[41])
    broken = tmp_path / "broken.sql"
    broken.write_text(migration.read_text(encoding="utf-8") + "\nSELECT missing_fixture_column;\n", encoding="utf-8")
    with patch.dict(user_data_base.USER_MIGRATIONS, {41: broken}):
        with pytest.raises(UserDataError):
            UserDataDao(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 40
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='character_profile_observation'").fetchone() is None
    with UserDataDao(database) as dao:
        assert dao.summary()["schema_version"] == 42


def test_cultivation_seed_uses_sparse_growth_without_inventing_skills_or_fork(tmp_path):
    from src.services.cultivation_planner_service import CultivationPlannerService

    database, service = setup(tmp_path)
    service.patch_native_profiles([{"character_id": 1003, "character_level": 70}], check=lambda: None)
    static_path = tmp_path / "frozen-static.sqlite3"
    planner = CultivationPlannerService(static_database_path=static_path, user_database_path=database)
    detail = SimpleNamespace(character=SimpleNamespace(character_id=1003, name_zh="fixture"), skills=())
    with patch.object(planner, "_load_detail", return_value=detail), patch(
        "src.services.cultivation_planner_service.load_template_growth_defaults", return_value=defaults((1003,)),
    ) as load_defaults:
        result = planner.load_seed(1003)
    assert (result.current_level, result.current_breakthrough_stage) == (70, 6)
    assert result.skills == () and result.fork is None
    load_defaults.assert_called_once_with((1003,), static_database_path=static_path.resolve())


def test_future_battle_freeze_uses_sparse_growth_and_does_not_change_prior_projection(tmp_path):
    from src.services.battle_report_persistence_service import BattleReportPersistenceService

    database, service = setup(tmp_path)
    service.patch_native_profiles([{"character_id": 1003, "character_level": 70}], check=lambda: None)
    static = SimpleNamespace(
        list_fork_templates=lambda: [], list_character_awaken_effects=lambda _id: [],
        list_character_graduation_templates=lambda: [{"character_id": 1003, "profile": {
            "character_level": 80, "breakthrough_stage": 6, "skill_levels": {"fixture": 9},
        }}],
    )
    with patch("src.services.battle_report_persistence_service.static_character_shape_profile_fields", return_value={}):
        with UserDataDao(database) as dao:
            first = BattleReportPersistenceService._load_effective_profiles(static_dao=static, user_dao=dao)
        service.patch_native_profiles([{"character_id": 1003, "character_level": 71}], check=lambda: None)
        with UserDataDao(database) as dao:
            second = BattleReportPersistenceService._load_effective_profiles(static_dao=static, user_dao=dao)
    assert first[1003]["character_level"] == 70 and second[1003]["character_level"] == 71
    assert first[1003]["field_sources"]["character_level"] == "native_observed"
    assert first[1003]["field_sources"]["skill_levels"] == "template"


def test_native_patch_requires_frozen_static_path_unless_test_loader_is_injected(tmp_path):
    database, _service = setup(tmp_path)
    service = OfficialRoleProfileService(database)
    with pytest.raises(ValueError, match="冻结"):
        service.patch_native_profiles([{"character_id": 1003, "character_level": 70}], check=lambda: None)
    with UserDataDao(database) as dao:
        assert dao.get_native_character_profile_observation(1003) is None
