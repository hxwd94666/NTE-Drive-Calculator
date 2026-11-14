# 验证公告日期维护只改三人注解、保持原始展示时间与其余表，并拒绝夹带其他数值变化。
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from src.domain.catalog.static_character_release_annotations import CHARACTER_RELEASE_SEEDS, RELEASE_EVIDENCE
from tools.game_data.refresh_character_release_metadata import build_candidate, digest, validate_candidate


def test_official_release_dates_are_independent_of_early_show_time():
    for identifier, expected in [(1042, '2026-09-24'), (1057, '2026-10-15'), (1090, '2026-11-05')]:
        seed = CHARACTER_RELEASE_SEEDS[identifier]
        assert seed.release_date == expected and seed.official_release_override
        assert all(RELEASE_EVIDENCE[k][0] == 'official' for k in seed.release_evidence_keys)


def test_metadata_candidate_replays_all_tables_and_rejects_forged_other_changes(tmp_path):
    baseline = Path(__file__).resolve().parents[2] / 'data/role_catalog'
    candidate = tmp_path / 'candidate'
    result = build_candidate(baseline, candidate)
    assert result['verified'] and result['tables_verified'] > 20
    database = candidate / 'game_static.sqlite3'
    with closing(sqlite3.connect(database)) as db:
        rows = dict(db.execute('SELECT character_id,mainland_release_date FROM character_release_annotation'))
        assert rows[1042] == '2026-09-24' and rows[1057] == '2026-10-15' and rows[1090] == '2026-11-05'
        # 六月是导出的展示字段，不当作十月实际开池日期，也不修改原始来源。
        assert db.execute('SELECT mainland_show_time FROM character WHERE character_id=1057').fetchone()[0] == '2026-06-18T06:30:00'
        assert rows.get(1074) is None  # 11 月 26 日才到的角色不借本次维护伪装成已上线。
        db.execute("UPDATE character SET name_zh='fixture tamper' WHERE character_id=1004")
        db.commit()
    forged_hash = digest(database)
    for file in ('manifest.json', 'game_ui/manifest.json', 'character_release_metadata_provenance.json'):
        path = candidate / file
        value = json.loads(path.read_text(encoding='utf8'))
        if file == 'manifest.json': value['database']['sha256'] = forged_hash
        elif file.startswith('game_ui'): value['database_sha256'] = forged_hash
        else: value['candidate_sha256'] = forged_hash
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    with pytest.raises(ValueError, match='以外的数据变化'):
        validate_candidate(candidate, baseline)


@pytest.mark.parametrize('fail_install', [False, True])
def test_metadata_promotion_preserves_registered_case_variant_and_failure_rolls_back(tmp_path, monkeypatch, fail_install):
    from types import SimpleNamespace
    from tools.game_data import promote_role_catalog as module
    candidate, target = tmp_path / 'candidate', tmp_path / 'target'
    for root, value in [(candidate, b'new'), (target, b'old')]:
        (root / 'game_ui').mkdir(parents=True)
        for relative in ('game_static.sqlite3', 'manifest.json', 'game_ui/manifest.json'):
            (root / relative).write_bytes(value)
    extra = target / 'game_ui/skills/extra.png'
    extra.parent.mkdir(); extra.write_bytes(b'preserve-original-extra')
    (candidate / 'game_ui/skills').mkdir()
    (candidate / 'game_ui/skills/EXTRA.png').write_bytes(b'preserve-original-extra')
    monkeypatch.setattr(module, 'read_role_catalog', lambda root: SimpleNamespace(
        asset_root=root / 'game_ui', dataset_id='fixture', sha256='fixture', scope='role_page',
        database_path=root / 'game_static.sqlite3'))
    monkeypatch.setattr(module, 'validate_role_assets', lambda *args: {'files': {'skills/EXTRA.png': {}}})
    replace = module.os.replace
    def guarded(source, destination):
        if fail_install and Path(source).name == 'new' and Path(destination) == target:
            raise OSError('fixture install failure')
        return replace(source, destination)
    monkeypatch.setattr(module.os, 'replace', guarded)
    if fail_install:
        with pytest.raises(OSError, match='fixture install failure'):
            module.promote_role_catalog({'database_path': candidate / 'game_static.sqlite3'}, target)
    else:
        assert module.promote_role_catalog({'database_path': candidate / 'game_static.sqlite3'}, target)['promoted']
    assert extra.read_bytes() == b'preserve-original-extra'
    assert (target / 'game_static.sqlite3').read_bytes() == (b'old' if fail_install else b'new')


def test_promotion_still_rejects_truly_unmanaged_files(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tools.game_data import promote_role_catalog as module
    candidate, target = tmp_path / 'candidate', tmp_path / 'target'
    target.mkdir(); (target / 'unknown.txt').write_text('preserve', encoding='utf8')
    monkeypatch.setattr(module, 'read_role_catalog', lambda root: SimpleNamespace(
        asset_root=root / 'game_ui', dataset_id='fixture', sha256='fixture'))
    monkeypatch.setattr(module, 'validate_role_assets', lambda *args: {'files': {}})
    with pytest.raises(ValueError, match='非托管文件'):
        module.promote_role_catalog({'database_path': candidate / 'game_static.sqlite3'}, target)
    assert (target / 'unknown.txt').read_text('utf8') == 'preserve'
