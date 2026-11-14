# 用真实 Windows 长短路径验证冻结、借用、提交与仓库双链路，链接和写占用仍拒绝。
from ctypes import WinDLL, create_unicode_buffer, get_last_error, WinError
from ctypes import wintypes
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
from threading import Event
from types import SimpleNamespace

import pytest

from src.domain.equipment.eligibility.management_settings import MatchingScope
from src.integrations.static_catalog._static_release_pin import WindowsStaticFilePin
from src.integrations.static_catalog.static_catalog_release import (
    StaticCatalogReleaseError, StaticCatalogReleaseReader, static_catalog_commit_unit, static_catalog_read_unit,
)
from src.services.inventory.management.management_settings import ManagementSettingsService
from src.services.inventory.management.warehouse_management_editor import generate_editor_rules
from src.services.inventory.recommendation.warehouse_recommendation_preview import build_preview
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.characters.official.test_static_read_unit import release_fixture
from tests.storage.test_user_data_inventory_dao import item
from tests.storage.test_user_data_inventory_dao import snapshot
from tests.storage.test_user_data_inventory_dao import stat


ROOT = Path(__file__).resolve().parents[2]
NTE_TEST_TIER = 'core'


def short_directory(path):
    kernel = WinDLL('kernel32', use_last_error=True)
    kernel.GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetShortPathNameW.restype = wintypes.DWORD
    buffer = create_unicode_buffer(32768)
    size = kernel.GetShortPathNameW(str(path), buffer, len(buffer))
    if not size or size >= len(buffer):
        raise WinError(get_last_error())
    result = Path(buffer.value)
    assert result != path, '本项需要支持真实短目录名的 Windows 临时目录'
    assert result.samefile(path)
    return result


@pytest.fixture
def paths(tmp_path):
    path = release_fixture(tmp_path / 'static release with long name')
    return path, short_directory(path.parent) / path.name


def test_alias_and_long_spelling_share_frozen_identity_and_borrowed_owner(paths):
    canonical, alias = paths
    reader = StaticCatalogReleaseReader()
    release = reader.freeze(alias)
    assert release.database_path == canonical
    assert release == reader.freeze(canonical)
    with reader.read_unit(alias, expected_release=release) as unit:
        with static_catalog_read_unit(alias, expected_release=release, read_unit=unit) as borrowed:
            assert borrowed is unit
            assert borrowed.dao._one('SELECT value FROM marker')['value'] == 'A'
        unit.ensure_unchanged()
    reader.ensure_unchanged(release)
    with static_catalog_commit_unit(alias, expected_release=release) as unit:
        assert unit.release == release


@pytest.mark.parametrize('short_writer', [False, True])
def test_writer_opened_through_either_spelling_still_blocks_pin(paths, short_writer):
    canonical, alias = paths
    with (alias if short_writer else canonical).open('r+b'):
        with pytest.raises(StaticCatalogReleaseError) as error:
            StaticCatalogReleaseReader().freeze(alias)
    assert error.value.__cause__.winerror == 32
    assert StaticCatalogReleaseReader().freeze(alias).database_path == canonical


def test_alias_still_rejects_sidecars_and_changed_manifest(paths):
    canonical, alias = paths
    sidecar = canonical.with_name(canonical.name + '-wal')
    sidecar.write_bytes(b'preserved fixture')
    with pytest.raises(StaticCatalogReleaseError) as caught:
        StaticCatalogReleaseReader().freeze(alias)
    assert caught.value.code == 'release_unclean' and sidecar.read_bytes() == b'preserved fixture'
    sidecar.unlink()
    with pytest.raises(StaticCatalogReleaseError) as caught:
        with StaticCatalogReleaseReader().read_unit(alias) as unit:
            canonical.with_name('manifest.json').write_text('{}', encoding='utf8')
            unit.ensure_unchanged()
    assert caught.value.code == 'release_changed'


def test_original_directory_junction_is_not_cleaned_into_an_allowed_path(paths, tmp_path):
    canonical, _ = paths
    junction = tmp_path / 'linked static directory'
    created = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(junction), str(canonical.parent)],
                             capture_output=True)
    assert created.returncode == 0
    try:
        with pytest.raises(OSError, match='重解析点'):
            WindowsStaticFilePin(junction / canonical.name)
        with StaticCatalogReleaseReader().read_unit(canonical) as unit:
            with pytest.raises(StaticCatalogReleaseError) as caught:
                unit.require_source(junction / canonical.name, None, None)
            assert caught.value.code == 'release_changed'
    finally:
        # 只移除本例创建的目录联接本身，不递归进入或删除目标资料目录。
        os.rmdir(junction)
    assert canonical.is_file()


def test_non_file_and_other_release_are_not_accepted_as_aliases(paths, tmp_path):
    canonical, _ = paths
    with pytest.raises(OSError, match='本机规则文件'):
        WindowsStaticFilePin(canonical.parent)
    other = release_fixture(tmp_path / 'other')
    with StaticCatalogReleaseReader().read_unit(canonical) as unit:
        with pytest.raises(StaticCatalogReleaseError) as caught:
            unit.require_source(other, None, None)
    assert caught.value.code == 'release_changed'


@pytest.mark.parametrize('action', ['preview', 'first_rules'])
def test_both_warehouse_flows_use_short_temp_directory_without_mutating_account(tmp_path, monkeypatch, action):
    static = ROOT / 'data/game_static.sqlite3'
    database, config_dir = tmp_path / 'user.sqlite3', tmp_path / 'config'
    config_dir.mkdir()
    shutil.copyfile(ROOT / 'config/stats.json', config_dir / 'stats.json')
    with StaticGameDataDao(static) as dao:
        geometry = dao.get_equipment_item(item(1, 1)['item_id'])['geometry_id']
    row = {**item(13, 23), 'geometry': geometry,
           'main_stats': [stat('AtkUp', .1, True), stat('HPMaxUp', .1, True)]}
    with UserDataDao(database, account_id='short-path-fixture') as dao:
        snapshot_id = dao.import_inventory_snapshot(snapshot(1, [row]))
    config, matching = ManagementSettingsService(database, scope=MatchingScope.WAREHOUSE).load()
    with UserDataDao.read_only(database) as dao:
        before = tuple(dao._db().iterdump())
    temp_root = tmp_path / 'Windows temporary directory with long name'
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, 'tempdir', str(short_directory(temp_root)))
    if action == 'preview':
        result, guard = build_preview(database=database, static_database=static,
            asset_root=ROOT / 'data/role_catalog/game_ui', config_dir=config_dir, config=config,
            matching=matching, check_context=lambda: None, cancel_check=lambda: False)
        assert result['snapshot_id'] == snapshot_id and len(result['rows']) == 1
        guard.require_current()
    else:
        request = SimpleNamespace(account_id='short-path-fixture', generation=1, database=database,
                                  static=static, config=config_dir)
        result = generate_editor_rules(request, config, matching, Event(), lambda: None)
        assert result['rules'] and result['options']['initial_grade_advice']['snapshot_id'] == snapshot_id
    with UserDataDao.read_only(database) as dao:
        assert tuple(dao._db().iterdump()) == before
    assert list(temp_root.iterdir()) == []
