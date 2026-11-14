# 覆盖全家具默认值、账号来源优先级、旧版手动保护、事务取消和独立面板映射。
import pytest
from pathlib import Path

from src.domain.characters.furniture import native_furniture_values
from src.domain.official_role.models import WorldBonusSettings, world_bonus_property_stats
from src.services.official_role.panel.world_bonus import WorldBonusSettingsService
from src.storage.sqlite.user_data_dao import UserDataDao


NTE_TEST_TIER = 'core'


@pytest.fixture
def account(tmp_path):
    path = tmp_path / 'account.sqlite3'
    with UserDataDao(path, account_id='furniture-fixture', account_name='fixture'):
        pass
    return path, WorldBonusSettingsService(path)


def test_all_combat_furniture_defaults_and_units():
    assert WorldBonusSettings().to_payload() == dict(yaodao_attack_add=20., quantao_crit_damage=.04,
                                                    kaijia_defense_add=30.)
    assert world_bonus_property_stats(None) == dict(AtkAdd=20., CritDamageBase=.04, DefAdd=30.)
    rows = [dict(kind='furniture_raw', FurnitureID=identity, Level=level, bIsActivated=True)
            for identity, level in [('SF_0011', 3), ('SF_0012', 4), ('SF_0013', 5)]]
    assert native_furniture_values(rows) == dict(yaodao_attack_add=6., quantao_crit_damage=.016,
                                               kaijia_defense_add=15.)


def test_native_is_saved_per_account_and_last_value_survives_new_service(account):
    path, service = account
    assert service.apply_native({'yaodao_attack_add': 6., 'kaijia_defense_add': 15.}, check=lambda: None)
    assert WorldBonusSettingsService(path).load() == WorldBonusSettings(6., .04, 15.)
    assert not service.apply_native({'yaodao_attack_add': 6., 'kaijia_defense_add': 15.}, check=lambda: None)


def test_manual_full_defaults_still_block_future_native_updates(account):
    path, service = account
    service.save(WorldBonusSettings())
    assert not service.apply_native(dict(yaodao_attack_add=0., quantao_crit_damage=0.,
                                        kaijia_defense_add=0.), check=lambda: None)
    assert service.load() == WorldBonusSettings()
    with UserDataDao(path) as dao:
        assert dao.list_application_setting_copies()['world_bonus']['source'] == 'manual'


def test_manual_overrides_existing_native_and_remains_after_reopen(account):
    path, service = account
    service.apply_native({'kaijia_defense_add': 6.}, check=lambda: None)
    service.save(WorldBonusSettings(12., .024, 18.))
    assert not WorldBonusSettingsService(path).apply_native({'kaijia_defense_add': 30.}, check=lambda: None)
    assert service.load() == WorldBonusSettings(12., .024, 18.)


def test_old_explicit_two_field_setting_is_manual_and_adds_full_defense(account):
    path, service = account
    with UserDataDao(path) as dao:
        dao.replace_application_setting_copy('world_bonus', dict(yaodao_attack_add=8., quantao_crit_damage=.02))
    assert not service.apply_native({'yaodao_attack_add': 20., 'kaijia_defense_add': 0.}, check=lambda: None)
    assert service.load() == WorldBonusSettings(8., .02, 30.)
    with UserDataDao(path) as dao:
        assert dao.list_application_setting_copies()['world_bonus'] == dict(yaodao_attack_add=8., quantao_crit_damage=.02)


def test_cancel_after_database_write_rolls_back_entire_furniture_update(account):
    path, service = account
    service.apply_native({'yaodao_attack_add': 10.}, check=lambda: None)
    calls = []
    def check():
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError('cancel fixture')
    with pytest.raises(RuntimeError, match='cancel fixture'):
        service.apply_native({'yaodao_attack_add': 0., 'kaijia_defense_add': 0.}, check=check)
    assert service.load() == WorldBonusSettings(10., .04, 30.)


@pytest.mark.parametrize('level,active', [(True, True), (-1, True), (11, True), (1.2, True), (4, 1)])
def test_unknown_furniture_fields_are_not_zero(level, active):
    row = dict(kind='furniture_raw', FurnitureID='SF_0013', Level=level, bIsActivated=active)
    assert native_furniture_values([row]) == {}


def test_unactivated_is_explicit_zero_and_duplicate_is_ignored():
    row = dict(kind='furniture_raw', FurnitureID='SF_0013', Level=5, bIsActivated=False)
    assert native_furniture_values([row]) == {'kaijia_defense_add': 0.}
    assert native_furniture_values([row, dict(row, Level=8)]) == {}


def test_old_frozen_world_mapping_does_not_invent_defense_observation():
    assert world_bonus_property_stats({'yaodao_attack_add': 8., 'quantao_crit_damage': .02}) == {
        'AtkAdd': 8., 'CritDamageBase': .02}


def test_account_isolation_and_invalid_native_values_keep_previous(account, tmp_path):
    path, first = account
    second_path = tmp_path / 'second.sqlite3'
    with UserDataDao(second_path, account_id='other', account_name='fixture'):
        pass
    first.apply_native({'kaijia_defense_add': 3.}, check=lambda: None)
    with pytest.raises(ValueError):
        first.apply_native({'kaijia_defense_add': float('nan')}, check=lambda: None)
    assert first.load().kaijia_defense_add == 3.
    assert WorldBonusSettingsService(second_path).load() == WorldBonusSettings()


def test_account_values_reach_role_and_saved_loadout_summaries_without_rewriting_frozen_input(account):
    from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
    from src.services.official_role.page import load_official_role_detail
    from src.services.official_role.calculation_values import freeze_role_calculation_detail
    from src.services.equipment.loadouts.saved_loadout_attribute_summary_service import load_saved_loadout_attribute_summaries

    path, service = account
    static = Path(__file__).resolve().parents[3] / 'data/game_static.sqlite3'
    with StaticGameDataDao(static) as dao:
        identity = int(dao.list_role_template_characters()[0]['character_id'])
    def detail():
        return load_official_role_detail(path, identity, include_inventory_contexts=False,
            include_replacement_candidates=False, static_database_path=static,
            shared_database_path=path.parent / 'unused-shared.sqlite3')
    before = detail()
    frozen = freeze_role_calculation_detail(before)
    def summary():
        value = load_saved_loadout_attribute_summaries(path, static, identity, [])
        return {row.key: row.value for row in value['character']}
    full = summary()
    assert service.apply_native(dict(yaodao_attack_add=6., quantao_crit_damage=.016,
                                    kaijia_defense_add=15.), check=lambda: None)
    current = summary()
    assert detail()['world_bonus'] == WorldBonusSettings(6., .016, 15.).to_payload()
    assert full['PanelAtk'] - current['PanelAtk'] == pytest.approx(14.)
    assert full['PanelDef'] - current['PanelDef'] == pytest.approx(15.)
    assert full['PanelCritDamage'] - current['PanelCritDamage'] == pytest.approx(.024)
    assert frozen['world_bonus'] == WorldBonusSettings().to_payload()
