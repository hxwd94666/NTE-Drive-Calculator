# 验证觉醒等级与实际选择独立同步、空选择覆盖及事务保护。
from pathlib import Path

import pytest

from src.services.native_role_profile_projection import project_native_role_profile
from src.services.official_role_awakening_service import active_awaken_effects, resolve_awakening_profile
from src.services.official_role_profile_service import OfficialRoleProfileService
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_native_role_profile_patch import setup, seed
from tests.test_official_role_awakening import _effects


def selection(effects, level=6):
    return dict(character_id=1008, awakening_level=level,
                awakening_selection_initialized=True, selected_awaken_effect_ids=effects)


def test_six_unlocked_five_selected_survives_save_reopen_clear_and_restore(tmp_path):
    database, service = setup(tmp_path)
    with UserDataDao(database) as dao:
        original = seed(dao, 1008)
    for effects in ([f'Effect{i}' for i in range(1, 6)], [], ['Effect6']):
        service.patch_native_profiles([selection(effects)], check=lambda: None)
        with UserDataDao(database) as dao:
            saved = dao.get_character_profile(1008)
            assert {k: v for k, v in saved.items() if k != 'updated_at_utc'} == {k: v for k, v in original.items() if k != 'updated_at_utc'}
            observed = dao.get_native_character_profile_observation(1008)
        projected = project_native_role_profile(original, observed, persisted=True)
        resolved = resolve_awakening_profile(projected, _effects())
        assert resolved['awakening_level'] == 6
        assert resolved['selected_awaken_effect_ids'] == effects
        assert resolved['field_sources']['selected_awaken_effect_ids'] == 'native_observed'
        active = {r['effect_id'] for r in active_awaken_effects(resolved, _effects())}
        assert active == set(effects) | {'resonance_3', 'resonance_6'}


def test_missing_and_incomplete_selection_preserve_previous_observation(tmp_path):
    database, service = setup(tmp_path)
    service.patch_native_profiles([selection(['Effect2'])], check=lambda: None)
    service.patch_native_profiles([{'character_id': 1008, 'character_level': 80,
                                   'awakening_selection_initialized': False,
                                   'selected_awaken_effect_ids': []}], check=lambda: None)
    with UserDataDao(database) as dao:
        observed = dao.get_native_character_profile_observation(1008)
    assert observed['selected_awaken_effect_ids'] == ['Effect2']
    assert observed['awakening_level'] == 6


@pytest.mark.parametrize('patch', [selection(['Effect1', 'Effect1']), selection(['Effect1'], 0),
                                 selection(None), selection([], True)])
def test_invalid_selection_preserves_valid_fields_in_batch(tmp_path, patch):
    database, service = setup(tmp_path)
    result = service.patch_native_profiles([{'character_id': 1003, 'character_level': 70}, patch], check=lambda: None)
    assert result.warnings
    with UserDataDao(database) as dao:
        assert dao.get_native_character_profile_observation(1003)['character_level'] == 70
        observed = dao.get_native_character_profile_observation(1008) or {}
        assert 'selected_awaken_effect_ids' not in observed


def test_shipped_catalog_validates_effects_and_base_levels(tmp_path):
    database, _ = setup(tmp_path)
    service = OfficialRoleProfileService(database, static_database_path=Path('data/game_static.sqlite3'))
    service.patch_native_profiles([selection(['Effect1', 'Effect5'])], check=lambda: None)
    result = service.patch_native_profiles([selection(['resonance_6'])], check=lambda: None)
    assert '官方目录' in result.message
    with UserDataDao(database) as dao:
        assert dao.get_native_character_profile_observation(1008)['selected_awaken_effect_ids'] == ['Effect1', 'Effect5']


def test_manual_profile_keeps_capacity_when_effects_are_cleared(tmp_path):
    database, _ = setup(tmp_path)
    with UserDataDao(database) as dao:
        seed(dao, 1008, awakening_level=6, selected_awaken_effect_ids=[])
        profile = dao.get_character_profile(1008)
    assert profile['awakening_level'] == 6
    assert profile['selected_awaken_effect_ids'] == []


def test_editor_unchecking_effect_does_not_reduce_awakening_level():
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication
    from src.features.official_role.role_awakening import _build_awakening_group
    from src.features.official_role.role_calculation import _calculation_detail

    app = QApplication.instance() or QApplication([])
    window = SimpleNamespace(_official_role_dirty_ids=set())
    detail = {'profile': {'awakening_level': 6, 'awakening_selection_initialized': True,
                          'selected_awaken_effect_ids': [f'Effect{i}' for i in range(1, 7)]},
              'awakenings': _effects()}
    editor = {}
    group = _build_awakening_group(window, 1008, detail, editor)
    editor['awakening_checks']['Effect6'].setChecked(False)
    projected = _calculation_detail(detail, editor)['profile']
    assert projected['awakening_level'] == 6
    assert len(projected['selected_awaken_effect_ids']) == 5
    for check in editor['awakening_checks'].values():
        check.setChecked(False)
    assert _calculation_detail(detail, editor)['profile']['awakening_level'] == 6
    assert _calculation_detail(detail, editor)['profile']['selected_awaken_effect_ids'] == []
    group.close()
    app.processEvents()


def test_battle_edit_preserves_level_instead_of_count():
    from src.storage.sqlite.battle_build_edit_dao import BattleBuildEditDaoMixin

    result = BattleBuildEditDaoMixin._normalize_battle_build_edit_profile({
        **selection(['Effect1']), 'character_level': 80, 'breakthrough_stage': 6,
        'skill_levels': {}, 'fork_id': None,
    })
    assert result['awakening_level'] == 6
    assert result['selected_awaken_effect_ids'] == ['Effect1']


def test_cancel_during_awakening_batch_rolls_back_and_can_retry(tmp_path):
    from tests.test_native_role_profile_patch import defaults

    database, service = setup(tmp_path)
    service.patch_native_profiles([selection(['Effect6'])], check=lambda: None)
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError('generation changed')

    patches = [selection([]), {**selection([]), 'character_id': 1003}]
    with UserDataDao(database) as dao:
        with pytest.raises(RuntimeError, match='generation'):
            dao.patch_native_character_profiles(patches, growth_defaults=defaults((1008, 1003)), check=cancel)
        assert dao.get_native_character_profile_observation(1008)['selected_awaken_effect_ids'] == ['Effect6']
        assert dao.get_native_character_profile_observation(1003) is None
    service.patch_native_profiles(patches, check=lambda: None)
    with UserDataDao(database) as dao:
        assert dao.get_native_character_profile_observation(1008)['selected_awaken_effect_ids'] == []
