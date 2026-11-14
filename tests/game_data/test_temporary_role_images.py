# 验证临时角色图片保留正式缺项、来源哈希和可见说明，不影响角色身份或分析输入。
import copy
import hashlib
import json

import pytest

from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.integrations.static_catalog.role_catalog_release import read_role_catalog
from tests.game_data.test_deferred_role_images import package
from tests.game_data.test_deferred_role_images import write

NTE_TEST_TIER = 'core'


def substituted(tmp_path):
    root, manifest, digest = package(tmp_path)
    target = root / 'temporary/characters/1074.png'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'fixture-image')
    sha = hashlib.sha256(target.read_bytes()).hexdigest().upper()
    source = '/Game/UI/UI/Gacha/Sideillustrate/sideillustrate_exe.sideillustrate_exe'
    manifest['characters']['1074'] = 'temporary/characters/1074.png'
    manifest['files']['temporary/characters/1074.png'] = {'sha256': sha, 'source_sha256': sha, 'source_asset_path': source}
    manifest['temporary_substitutions'] = {'characters:1074': {
        'original_source': manifest['unresolved_assets'][0]['source'], 'source_asset_path': source,
        'source_sha256': sha, 'notice': '临时替代头像：抽卡侧边人像；默认外观正式图片补齐后替换。'}}
    return root, manifest, digest


def test_explicit_substitute_is_visible_and_retains_missing_original(tmp_path):
    root, manifest, _ = substituted(tmp_path)
    write(root, manifest)
    assert read_role_catalog(tmp_path).dataset_id == 'fixture'
    catalog = GameUiAssetCatalog(root)
    assert catalog.character_icon(1074) == root / 'temporary/characters/1074.png'
    assert '临时替代头像' in catalog.character_image_note(1074, 'characters')
    assert catalog.character_image_note(1003) == ''
    assert json.loads((root / 'manifest.json').read_text('utf-8'))['unresolved_assets']


@pytest.mark.parametrize('bad', ['unmarked', 'unlabelled', 'wrong_original', 'wrong_source_hash', 'orphan', 'skill', 'mapped_missing_file'])
def test_substitute_cannot_hide_other_gaps_or_fake_provenance(tmp_path, bad):
    root, manifest, _ = substituted(tmp_path)
    entry = manifest['temporary_substitutions']['characters:1074']
    if bad == 'unmarked':
        manifest.pop('temporary_substitutions')
    elif bad == 'unlabelled':
        entry['notice'] = '正式头像'
    elif bad == 'wrong_original':
        entry['original_source'] = 'other.png'
    elif bad == 'wrong_source_hash':
        entry['source_sha256'] = '0' * 64
    elif bad == 'orphan':
        manifest['temporary_substitutions']['characters:1003'] = copy.deepcopy(entry)
    elif bad == 'skill':
        manifest['unresolved_assets'][0]['group'] = 'skills'
        manifest['deferred_optional_assets'][0]['group'] = 'skills'
    else:
        manifest['files'].clear()
    write(root, manifest)
    with pytest.raises(ValueError):
        read_role_catalog(tmp_path)


def test_default_image_rebuild_removes_temporary_status(tmp_path):
    root, manifest, _ = substituted(tmp_path)
    manifest['unresolved_assets'] = []
    manifest['deferred_optional_assets'] = []
    manifest.pop('temporary_substitutions')
    manifest['characters']['1074'] = 'characters/1074.png'
    target = root / 'characters/1074.png'
    target.parent.mkdir()
    target.write_bytes(b'default-image-fixture')
    manifest['files']['characters/1074.png'] = {'sha256': hashlib.sha256(target.read_bytes()).hexdigest().upper()}
    write(root, manifest)
    assert read_role_catalog(tmp_path).dataset_id == 'fixture'
    assert GameUiAssetCatalog(root).character_image_note(1074) == ''


def test_temporary_avatar_view_preserves_source_and_labels_team_button(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from src.domain.teams.models import TeamRole
    from src.features.teams.cards import role_button
    from src.ui.roles.role_portrait import temporary_role_portrait
    app = QApplication.instance() or QApplication([])
    original = tmp_path / 'wide-transparent-fixture.png'
    image = QImage(70, 25, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.fillRect(QRect(2, 0, 26, 23), QColor('blue'))
    painter.end()
    assert image.save(str(original))
    before = original.read_bytes()
    icon = temporary_role_portrait(str(original), 36, 2.0)
    assert icon.width() == 72 and icon.height() == 72 and icon.devicePixelRatio() == 2.0
    button = role_button(TeamRole(1074, 'Exe', str(original), icon_note='临时替代头像：抽卡侧边人像'))
    assert '临时替代头像' in button.toolTip() and not button.icon().isNull()
    button.deleteLater()
    app.processEvents()
    assert original.read_bytes() == before
