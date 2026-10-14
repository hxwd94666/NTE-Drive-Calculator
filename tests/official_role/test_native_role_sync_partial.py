# 验证角色同步跳过无效字段、保留旧值并保存其他正常数据。
from pathlib import Path
import json
import sqlite3

import pytest

from src.services.official_role.profiles import OfficialRoleProfileService
from src.integrations.native_inventory_snapshot import read_native_projection
from src.services.official_role.awakening import active_awaken_effects
from src.services.official_role.profile_projection import resolve_official_role_profile
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataError
from tests.test_native_role_profile_patch import seed, setup


def test_unknown_forks_report_each_identity_and_preserve_existing_fork(tmp_path):
    database, _ = setup(tmp_path)
    with UserDataDao(database) as dao:
        before = seed(dao, 1023)
    service = OfficialRoleProfileService(database, static_database_path=Path('data/game_static.sqlite3'))
    profiles = [dict(character_id=cid, character_level=60, breakthrough_stage=4,
                     fork_observed=True, fork_id=f'fork_unknown_{cid}', fork_level=40,
                     fork_breakthrough_stage=3, fork_refinement_level=2)
                for cid in (1023, 1004)]
    result = service.patch_native_profiles(profiles, check=lambda: None)
    assert result.saved_count == 2
    assert len(result.warnings) == 2
    assert all(f'fork_unknown_{cid}' in result.message for cid in (1023, 1004))
    with UserDataDao(database) as dao:
        after = dao.get_character_profile(1023)
        for key in ('fork_id', 'fork_level', 'fork_breakthrough_stage', 'fork_refinement_level'):
            assert after[key] == before[key]
        for cid in (1023, 1004):
            observed = dao.get_native_character_profile_observation(cid)
            assert observed['character_level'] == 60
            assert 'fork_id' not in observed
    assert profiles[0]['fork_id'] == 'fork_unknown_1023'


def test_bad_skill_does_not_block_valid_skill_or_other_cultivation(tmp_path):
    database, _ = setup(tmp_path)
    service = OfficialRoleProfileService(database, static_database_path=Path('data/game_static.sqlite3'))
    result = service.patch_native_profiles([{
        'character_id': 1023, 'skill_levels': {'GA_Cang_QTE': 4, 'unknown_skill': 2},
        'awakening_level': 2, 'likeability_levels': {'1023': 2},
    }], check=lambda: None)
    assert result.saved_count == 1
    assert 'unknown_skill' in result.message
    with UserDataDao(database) as dao:
        observed = dao.get_native_character_profile_observation(1023)
    assert observed['skill_levels'] == {'GA_Cang_QTE': 4}
    assert observed['awakening_level'] == 2
    assert observed['likeability_level_10_enabled'] is False


def test_growth_conflict_keeps_old_growth_but_saves_independent_fields(tmp_path):
    database, service = setup(tmp_path)
    with UserDataDao(database) as dao:
        seed(dao)
    result = service.patch_native_profiles([{
        'character_id': 1003, 'character_level': 10, 'likeability_level_10_enabled': False,
    }], check=lambda: None)
    assert result.saved_count == 1 and result.warnings
    with UserDataDao(database) as dao:
        profile = dao.get_character_profile(1003)
    assert profile['character_level'] == 60 and profile['breakthrough_stage'] == 5
    assert profile['likeability_level_10_enabled'] is False


def test_skipped_only_role_is_not_counted_or_created(tmp_path):
    database, service = setup(tmp_path)
    result = service.patch_native_profiles([{
        'character_id': 1003, 'fork_observed': True, 'fork_id': 'incomplete',
    }], check=lambda: None)
    assert result.saved_count == 0 and result.warnings
    assert '未写入新的角色状态' in result.message
    with UserDataDao(database) as dao:
        assert dao.list_native_character_profile_observations() == []


def test_conflicting_duplicate_role_does_not_block_another_role(tmp_path):
    database, service = setup(tmp_path)
    result = service.patch_native_profiles([
        {'character_id': 1003, 'character_level': 70},
        {'character_id': 1003, 'character_level': 80},
        {'character_id': 1004, 'character_level': 70},
    ], check=lambda: None)
    assert result.saved_count == 1 and result.warnings
    with UserDataDao(database) as dao:
        assert dao.get_native_character_profile_observation(1003) is None
        assert dao.get_native_character_profile_observation(1004)['character_level'] == 70


def test_database_failure_still_rolls_back_valid_fields_and_allows_retry(tmp_path):
    database, service = setup(tmp_path)
    with UserDataDao(database) as dao:
        before = seed(dao)
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TRIGGER reject_second_role BEFORE INSERT ON character_profile_observation
                              WHEN NEW.character_id = 1004
                              BEGIN SELECT RAISE(ABORT, 'fixture write failure'); END""")
    rows = [{'character_id': 1003, 'character_level': 61, 'skill_levels': {'bad': True}},
            {'character_id': 1004, 'character_level': 70}]
    with pytest.raises(UserDataError, match='无法保存'):
        service.patch_native_profiles(rows, check=lambda: None)
    with UserDataDao(database) as dao:
        assert dao.get_character_profile(1003) == before
        assert dao.list_native_character_profile_observations() == []
    with sqlite3.connect(database) as connection:
        connection.execute('DROP TRIGGER reject_second_role')
    result = service.patch_native_profiles(rows, check=lambda: None)
    assert result.saved_count == 2 and result.warnings


def test_unknown_character_does_not_block_known_character(tmp_path):
    database, _ = setup(tmp_path)
    service = OfficialRoleProfileService(database, static_database_path=Path('data/game_static.sqlite3'))
    result = service.patch_native_profiles([
        {'character_id': 999999, 'character_level': 70},
        {'character_id': 1023, 'character_level': 70},
    ], check=lambda: None)
    assert result.saved_count == 1 and '999999' in result.message
    with UserDataDao(database) as dao:
        assert dao.get_native_character_profile_observation(999999) is None
        assert dao.get_native_character_profile_observation(1023)['character_level'] == 70


@pytest.mark.parametrize('selection_source', ['account', 'native'])
@pytest.mark.parametrize('explicit_empty', [False, True])
def test_awakening_conflict_preserves_selection_and_saves_other_fields(tmp_path, selection_source, explicit_empty):
    database, _ = setup(tmp_path)
    static_path = Path('data/game_static.sqlite3')
    service = OfficialRoleProfileService(database, static_database_path=static_path)
    with StaticGameDataDao(static_path) as static:
        character = static.get_character(1023)
        growth = static.list_character_panel_growth(1023)
        awakenings = static.list_character_awaken_effects(1023)
    selected = [row['effect_id'] for row in awakenings if row['awaken_type'] == 'Awaken_Effect'][:2]
    assert len(selected) == 2
    with UserDataDao(database) as dao:
        seed(dao, 1023, awakening_level=2, selected_awaken_effect_ids=selected,
             fork_id=None, fork_level=None, fork_breakthrough_stage=None,
             fork_refinement_level=None, skill_levels={}, selected_skill_id=None)
    if selection_source == 'native':
        service.patch_native_profiles([{
            'character_id': 1023, 'awakening_level': 2, 'awakening_selection_initialized': True,
            'selected_awaken_effect_ids': selected,
        }], check=lambda: None)

    # 复用正式分页形状，仅替换本次合成角色 DTO；缺失选择不被解释成清空。
    fixture = json.loads((Path('tests/fixtures') / 'native_business_222.json').read_text(encoding='utf-8'))
    assert fixture['synthetic'] is True
    header = fixture['character']['refresh']['result']
    page = fixture['character']['response']['result']
    page['profiles'] = [{'character_id': 1023, 'awakening_level': 0, 'character_level': 70}]
    if explicit_empty:
        page['profiles'][0].update(awakening_selection_initialized=True, selected_awaken_effect_ids=[])
    payload = read_native_projection(lambda _method, _params: page, lambda: None, domain='character', header=header)
    result = service.patch_native_profiles(payload['profiles'], check=lambda: None)
    with UserDataDao(database) as dao:
        saved = dao.get_character_profile(1023)
        observed = dao.get_native_character_profile_observation(1023)
    projected = resolve_official_role_profile(
        character, growth, [], [], awakenings, saved_profile=saved,
        observation=observed, likeability_bonus=None,
    )
    active = [row['effect_id'] for row in active_awaken_effects(projected, awakenings)]
    expected = [] if explicit_empty else selected
    assert (projected['awakening_level'], active) == (len(expected), expected)
    assert projected['selected_awaken_effect_ids'] == expected
    assert result.saved_count == 1
    assert bool(result.warnings) is not explicit_empty
    assert saved['selected_awaken_effect_ids'] == selected
    assert observed['character_level'] == 70
    assert projected['character_level'] == 70
