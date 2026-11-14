# 仅刷新已核实官方公告的角色上线注解，逐表重放校验后经正式晋升入口成套安装图鉴。
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from itertools import zip_longest

from src.domain.catalog.static_character_release_annotations import CHARACTER_RELEASE_SEEDS, RELEASE_EVIDENCE
from src.integrations.static_catalog.role_catalog_release import read_role_catalog, validate_role_assets
from tools.game_data.upgrade_static_database import table_names

PROVENANCE = 'character_release_metadata_provenance.json'
CHARACTERS = (1042, 1057, 1090)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _apply(connection):
    """不改角色原始展示字段、公式、养成或导入器身份；本操作独立记录元数据维护版本。"""
    for identifier in CHARACTERS:
        seed = CHARACTER_RELEASE_SEEDS[identifier]
        if not seed.official_release_override or len(seed.release_evidence_keys) != 1:
            raise ValueError('发行注解缺少唯一官方公告')
        evidence = seed.release_evidence_keys[0]
        kind, locator = RELEASE_EVIDENCE[evidence]
        if kind != 'official':
            raise ValueError('上线日期维护只接受官方公告')
        if connection.execute('SELECT COUNT(*) FROM character_release_annotation WHERE character_id=?', (identifier,)).fetchone()[0] != 1:
            raise ValueError('待维护角色缺少图鉴注解，须完整构建目录')
        connection.execute('INSERT INTO character_release_evidence VALUES (?,?,?) ON CONFLICT(evidence_key) '
                           'DO UPDATE SET source_kind=excluded.source_kind, locator=excluded.locator', (evidence, kind, locator))
        connection.execute('UPDATE character_release_annotation SET mainland_release_date=?,release_source_kind=?,official_source_row_id=NULL '
                           'WHERE character_id=?', (seed.release_date, kind, identifier))
        connection.execute("DELETE FROM character_release_evidence_link WHERE character_id=? AND field_name='mainland_release_date'", (identifier,))
        connection.execute('INSERT INTO character_release_evidence_link VALUES (?,?,?,?)',
                           (identifier, 'mainland_release_date', 0, evidence))


def _readonly(database):
    return sqlite3.connect(database.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)


def _manifest_updates(baseline, database):
    manifest = json.loads((baseline / 'manifest.json').read_text(encoding='utf8'))
    manifest['database'].update(sha256=digest(database), size_bytes=database.stat().st_size)
    manifest['metadata_revision'] = dict(operation='character-release-v1',
        characters={str(i): CHARACTER_RELEASE_SEEDS[i].release_date for i in CHARACTERS})
    return manifest


def build_candidate(baseline, candidate):
    baseline, candidate = baseline.resolve(), candidate.resolve()
    release = read_role_catalog(baseline)
    if candidate.exists() or candidate == baseline or baseline in candidate.parents:
        raise ValueError('使用新的隔离候选目录，不覆盖正式目录或旧候选')
    candidate.mkdir(parents=True)
    database = candidate / 'game_static.sqlite3'
    with closing(_readonly(release.database_path)) as source, closing(sqlite3.connect(database)) as dest:
        source.backup(dest)
        _apply(dest)
        dest.commit()
    shutil.copytree(baseline / 'game_ui', candidate / 'game_ui')
    assets_path = candidate / 'game_ui/manifest.json'
    assets = json.loads(assets_path.read_text(encoding='utf8'))
    assets['database_sha256'] = digest(database)
    assets_path.write_text(json.dumps(assets, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    (candidate / 'manifest.json').write_text(json.dumps(_manifest_updates(baseline, database), ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    provenance = dict(operation='character-release-v1', baseline_sha256=release.sha256,
        candidate_sha256=digest(database), maintained_at_utc=datetime.now(timezone.utc).isoformat(),
        characters={str(i): CHARACTER_RELEASE_SEEDS[i].release_date for i in CHARACTERS})
    (candidate / PROVENANCE).write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    return validate_candidate(candidate, baseline)


def validate_candidate(candidate, baseline):
    """从当前正式库重放唯一允许的三条注解修改，全部表和图片逐项等价才允许晋升。"""
    candidate, baseline = candidate.resolve(), baseline.resolve()
    before, after = read_role_catalog(baseline), read_role_catalog(candidate)
    provenance = json.loads((candidate / PROVENANCE).read_text(encoding='utf8'))
    expected_characters = {str(i): CHARACTER_RELEASE_SEEDS[i].release_date for i in CHARACTERS}
    if (provenance.get('operation') != 'character-release-v1' or provenance.get('baseline_sha256') != before.sha256
            or provenance.get('candidate_sha256') != after.sha256 or provenance.get('characters') != expected_characters):
        raise ValueError('上线注解候选或基线已变化，请重新生成')
    if json.loads((candidate / 'manifest.json').read_text(encoding='utf8')) != _manifest_updates(baseline, after.database_path):
        raise ValueError('元数据维护改变了目录身份或构建能力')
    old_assets = validate_role_assets(before.asset_root, before.dataset_id, before.sha256)
    new_assets = validate_role_assets(after.asset_root, after.dataset_id, after.sha256)
    old_assets['database_sha256'] = new_assets['database_sha256']
    if old_assets != new_assets:
        raise ValueError('上线注解维护不得改变公共图片或技能关联')
    with closing(_readonly(before.database_path)) as source, closing(sqlite3.connect(':memory:')) as expected, closing(_readonly(after.database_path)) as actual:
        source.backup(expected)
        _apply(expected)
        if actual.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or actual.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('候选数据库完整性或外键校验失败')
        if list(actual.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")) != list(expected.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")):
            raise ValueError('上线注解维护不得修改 schema')
        tables = table_names(actual)
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = ['"' + row[1].replace('"', '""') + '"' for row in actual.execute(f'PRAGMA table_info({quoted})')]
            sql = f'SELECT * FROM {quoted} ORDER BY ' + ','.join(columns)
            # 公共图鉴包含压缩 BLOB，逐个原始 SQLite 值比较，不对二进制值做 JSON 序列化。
            if any(a != e for a, e in zip_longest(actual.execute(sql), expected.execute(sql))):
                raise ValueError('候选包含上线注解以外的数据变化：' + table)
    return dict(verified=True, database_path=after.database_path, baseline_sha256=before.sha256,
                candidate_sha256=after.sha256, tables_verified=len(tables), characters=expected_characters)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, default=Path('data/role_catalog'))
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--promote', action='store_true')
    args = parser.parse_args()
    result = build_candidate(args.baseline, args.candidate)
    if args.promote:
        from tools.game_data.promote_static_release import promote_character_release_metadata
        result = promote_character_release_metadata(args.candidate, args.baseline)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
