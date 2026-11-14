# 验证仅显式延期的正式角色头像与立绘可缺省，其余来源、身份和文件校验保持严格。
from contextlib import closing
import copy
import hashlib
import json
import sqlite3

import pytest

from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.integrations.static_catalog.role_catalog_release import read_role_catalog, validate_role_assets

NTE_TEST_TIER = 'core'


def package(tmp_path):
    database = tmp_path / 'game_static.sqlite3'
    with closing(sqlite3.connect(database)) as db:
        db.executescript("CREATE TABLE dataset(dataset_id TEXT); CREATE TABLE dataset_scope(dataset_id TEXT,scope TEXT);"
                         "CREATE TABLE character(character_id INTEGER); INSERT INTO character VALUES(1074);"
                         "INSERT INTO dataset VALUES('fixture'); INSERT INTO dataset_scope VALUES('fixture','reference');")
        db.commit()
    digest = hashlib.sha256(database.read_bytes()).hexdigest().upper()
    (tmp_path / 'manifest.json').write_text(json.dumps({'catalog_scope': 'reference', 'database': {
        'filename': database.name, 'dataset_id': 'fixture', 'sha256': digest, 'size_bytes': database.stat().st_size}}), encoding='utf-8')
    root = tmp_path / 'game_ui'
    root.mkdir()
    gap = {'group': 'characters', 'id': '1074', 'source': 'UI/UI_Icon/Appearance/1074/Fashion_1074_0/256_Fashion_1074_0.png',
           'reason': 'missing_same_version_source'}
    manifest = {'dataset_id': 'fixture', 'database_sha256': digest, 'files': {}, 'characters': {}, 'character_arts': {},
                'skills': {}, 'fork_items': {}, 'unresolved_assets': [gap], 'deferred_optional_assets': [copy.deepcopy(gap)]}
    return root, manifest, digest


def write(root, manifest):
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')


def test_accepted_optional_gap_retains_identity_and_missing_image(tmp_path):
    root, manifest, digest = package(tmp_path)
    write(root, manifest)
    assert validate_role_assets(root, 'fixture', digest)['unresolved_assets'] == manifest['unresolved_assets']
    assert read_role_catalog(tmp_path).dataset_id == 'fixture'
    assert GameUiAssetCatalog(root).character_icon(1074) is None


@pytest.mark.parametrize('case', ['unaccepted', 'skill', 'unknown_group', 'duplicate', 'source_mismatch',
                                 'traversal', 'bad_identity', 'mapped_placeholder', 'stale_acceptance', 'unknown_character'])
def test_deferral_is_exact_optional_and_bound_to_formal_character(tmp_path, case):
    root, manifest, _ = package(tmp_path)
    gap = manifest['unresolved_assets'][0]
    accepted = manifest['deferred_optional_assets'][0]
    if case == 'unaccepted':
        manifest['deferred_optional_assets'] = []
    elif case == 'skill':
        gap['group'] = accepted['group'] = 'skills'
    elif case == 'unknown_group':
        gap['group'] = accepted['group'] = 'equipment_items'
    elif case == 'duplicate':
        manifest['unresolved_assets'].append(copy.deepcopy(gap))
        manifest['deferred_optional_assets'].append(copy.deepcopy(accepted))
    elif case == 'source_mismatch':
        accepted['source'] = accepted['source'].replace('256_', '2560_')
    elif case == 'traversal':
        gap['source'] = accepted['source'] = '../image.png'
    elif case == 'bad_identity':
        gap['id'] = accepted['id'] = 'nickname'
    elif case == 'mapped_placeholder':
        (root / 'fake.png').write_bytes(b'fixture')
        manifest['characters']['1074'] = 'fake.png'
        manifest['files']['fake.png'] = {'sha256': hashlib.sha256(b'fixture').hexdigest().upper()}
    elif case == 'stale_acceptance':
        manifest['unresolved_assets'] = []
    else:
        with closing(sqlite3.connect(tmp_path / 'game_static.sqlite3')) as db:
            db.execute('DELETE FROM character')
            db.commit()
        digest = hashlib.sha256((tmp_path / 'game_static.sqlite3').read_bytes()).hexdigest().upper()
        manifest['database_sha256'] = digest
        release = json.loads((tmp_path / 'manifest.json').read_text('utf-8'))
        release['database']['sha256'] = digest
        release['database']['size_bytes'] = (tmp_path / 'game_static.sqlite3').stat().st_size
        (tmp_path / 'manifest.json').write_text(json.dumps(release), encoding='utf-8')
    write(root, manifest)
    with pytest.raises(ValueError):
        read_role_catalog(tmp_path)


def test_deferred_image_does_not_exempt_other_file_hashes(tmp_path):
    root, manifest, _ = package(tmp_path)
    manifest['files']['existing.png'] = {'sha256': '0' * 64}
    (root / 'existing.png').write_bytes(b'broken')
    write(root, manifest)
    with pytest.raises(ValueError):
        read_role_catalog(tmp_path)
