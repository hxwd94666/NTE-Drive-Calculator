# 验证技能、好感度和弧盘同步的保存、页面投影与未知字段保护。
import pytest
import sqlite3

from src.services.native_role_profile_projection import project_native_role_profile
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError
from tests.test_native_role_profile_patch import setup, seed


def test_cultivation_is_saved_and_projected_without_manual_profile(tmp_path):
    database, service = setup(tmp_path)
    service.patch_native_profiles([{
        'character_id': 1003, 'skill_levels': {'skill_fixture': 6},
        'likeability_level_10_enabled': False, 'fork_observed': True,
        'fork_id': 'fork_fixture', 'fork_level': 40, 'fork_breakthrough_stage': 3,
        'fork_refinement_level': 2,
    }], check=lambda: None)
    with UserDataDao(database) as dao:
        observation = dao.get_native_character_profile_observation(1003)
        assert dao.get_character_profile(1003) is None
    base = {'character_level': 80, 'breakthrough_stage': 6, 'skill_levels': {'skill_fixture': 9, 'other': 8},
            'likeability_level_10_enabled': True, 'fork_id': 'old'}
    result = project_native_role_profile(base, observation, persisted=False)
    assert result['skill_levels'] == {'skill_fixture': 6, 'other': 8}
    assert result['likeability_level_10_enabled'] is False
    assert result['fork_id'] == 'fork_fixture' and result['fork_level'] == 40
    assert result['field_sources']['fork_id'] == 'native_observed'
    assert base['skill_levels']['skill_fixture'] == 9


def test_saved_profile_updates_and_explicit_unequip_clears_fork(tmp_path):
    database, service = setup(tmp_path)
    with UserDataDao(database) as dao:
        seed(dao)
    service.patch_native_profiles([{'character_id': 1003, 'skill_levels': {'skill_fixture': 6},
                                   'likeability_level_10_enabled': False,
                                   'fork_observed': True, 'fork_id': None}], check=lambda: None)
    service.patch_native_profiles([{'character_id': 1003, 'character_level': 61}], check=lambda: None)
    with UserDataDao(database) as dao:
        profile = dao.get_character_profile(1003)
        assert profile['skill_levels']['skill_fixture'] == 6
        assert profile['fork_id'] is None and profile['likeability_level_10_enabled'] is False
        assert profile['selected_awaken_effect_ids'] == ['awaken_fixture']
        assert dao.get_native_character_profile_observation(1003)['fork_id'] is None


@pytest.mark.parametrize('fields', [
    {'skill_levels': {'skill_fixture': True}}, {'likeability_level_10_enabled': 0},
    {'fork_observed': True, 'fork_id': 'fork_fixture', 'fork_level': 40},
])
def test_invalid_cultivation_does_not_partially_save(tmp_path, fields):
    database, service = setup(tmp_path)
    with pytest.raises(UserDataValidationError):
        service.patch_native_profiles([{'character_id': 1003, 'character_level': 61, **fields}], check=lambda: None)
    with UserDataDao(database) as dao:
        assert dao.get_native_character_profile_observation(1003) is None


def test_v41_upgrade_keeps_observed_growth_and_failed_v42_can_retry(tmp_path):
    from src.storage.sqlite import user_data_base
    from unittest.mock import patch
    database, _ = setup(tmp_path)
    with sqlite3.connect(database) as conn:
        conn.execute('DROP TABLE character_profile_observation')
        conn.executescript(user_data_base.USER_MIGRATIONS[41].read_text(encoding='utf-8'))
        conn.execute("INSERT INTO character_profile_observation VALUES (1003,60,4,'2026-01-01T00:00:00Z')")
        conn.execute('DELETE FROM schema_migration WHERE version=42')
    broken = tmp_path / 'broken_v42.sql'
    broken.write_text(user_data_base.USER_MIGRATIONS[42].read_text(encoding='utf-8') + '\nINVALID SQL;', encoding='utf-8')
    with patch.dict(user_data_base.USER_MIGRATIONS, {42: broken}), pytest.raises(Exception):
        with UserDataDao(database):
            pass
    with sqlite3.connect(database) as conn:
        assert conn.execute('SELECT character_level FROM character_profile_observation').fetchone()[0] == 60
        assert conn.execute('SELECT MAX(version) FROM schema_migration').fetchone()[0] == 41
    with UserDataDao(database) as dao:
        assert dao.get_native_character_profile_observation(1003)['breakthrough_stage'] == 4
        assert dao.summary()['schema_version'] == 42


def test_likeability_uses_formal_alias_and_not_character_id():
    from types import SimpleNamespace
    from src.services.native_role_profile_projection import resolve_native_likeability
    profile = {'character_id': 1052, 'likeability_levels': {'1052': 2, '1029': 10}}
    static = SimpleNamespace(get_character_likeability_identity=lambda _: {'likeability_id': '1029', 'required_level': 10})
    resolve_native_likeability(profile, static)
    assert profile['likeability_level_10_enabled'] is True
    assert 'likeability_levels' not in profile


def test_unknown_fork_does_not_clear_existing_battle_profile():
    profile = {'character_level': 60, 'breakthrough_stage': 4, 'fork_id': 'existing'}
    projected = project_native_role_profile(profile, {'fork_id': None}, persisted=True)
    assert projected['fork_id'] == 'existing'


def test_full_service_resolves_shipped_skill_fork_and_likeability_ids(tmp_path):
    from pathlib import Path
    from src.services.official_role_profile_service import OfficialRoleProfileService
    database, _ = setup(tmp_path)
    static_path = Path(__file__).resolve().parents[1] / 'data/game_static.sqlite3'
    service = OfficialRoleProfileService(database, static_database_path=static_path)
    service.patch_native_profiles([{
        'character_id': 1023, 'character_level': 60, 'breakthrough_stage': 4,
        'skill_levels': {'GA_Cang_Melee': 6, 'GA_Cang_Skill': 6, 'GA_Cang_UltraSkill': 6, 'GA_Cang_QTE': 4},
        'likeability_levels': {'1023': 2}, 'fork_observed': True,
        'fork_id': 'fork_jingmotingyuan', 'fork_level': 60, 'fork_breakthrough_stage': 4, 'fork_refinement_level': 3,
    }], check=lambda: None)
    with UserDataDao(database) as dao:
        observed = dao.get_native_character_profile_observation(1023)
    assert observed['skill_levels']['GA_Cang_QTE'] == 4
    assert observed['likeability_level_10_enabled'] is False
    assert observed['fork_refinement_level'] == 3


def test_maximum_base_skill_level_is_one_above_last_upgrade_cost(tmp_path):
    from pathlib import Path
    from src.services.official_role_profile_service import OfficialRoleProfileService
    database, _ = setup(tmp_path)
    service = OfficialRoleProfileService(database, static_database_path=Path(__file__).resolve().parents[1] / 'data/game_static.sqlite3')
    # A maxed skill must not prevent another character's valid cultivation
    # from being committed in the same batch.
    service.patch_native_profiles([
        {'character_id': 1004, 'skill_levels': {'GA_Lacrimosa_Melee': 10}},
        {'character_id': 1023, 'skill_levels': {'GA_Cang_QTE': 4}},
    ], check=lambda: None)
    with UserDataDao(database) as dao:
        assert dao.get_native_character_profile_observation(1004)['skill_levels']['GA_Lacrimosa_Melee'] == 10
        assert dao.get_native_character_profile_observation(1023)['skill_levels']['GA_Cang_QTE'] == 4
    with pytest.raises(UserDataValidationError, match='基础等级'):
        service.patch_native_profiles([
            {'character_id': 1023, 'skill_levels': {'GA_Cang_QTE': 5}},
            {'character_id': 1004, 'skill_levels': {'GA_Lacrimosa_Melee': 11}},
        ], check=lambda: None)
    with UserDataDao(database) as dao:
        assert dao.get_native_character_profile_observation(1023)['skill_levels']['GA_Cang_QTE'] == 4
