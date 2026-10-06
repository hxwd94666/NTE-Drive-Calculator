# 从同版游戏资源按正式角色技能 ID 构建公共图片候选，保留原图与逐文件来源哈希。
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def role_skill_asset_requests(database: Path) -> list[tuple[str, str | None]]:
    """List the same formal active skills consumed by the share service."""
    database = database.resolve()
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as connection:
        return connection.execute('''
            SELECT DISTINCT skill.skill_id, ability.icon_path
            FROM character_skill AS skill
            LEFT JOIN gameplay_ability_catalog AS ability ON ability.ability_id = skill.skill_id
            WHERE LOWER(skill.ability_type) <> 'passive'
            ORDER BY skill.skill_id
        ''').fetchall()


def add_role_skill_assets(source: Path, database: Path, output: Path) -> dict:
    """Augment a candidate UI manifest; never import Bot mappings or change the DB."""
    source, database, output = source.resolve(), database.resolve(), output.resolve()
    path = output/'manifest.json'
    manifest = json.loads(path.read_text(encoding='utf-8'))
    rows = role_skill_asset_requests(database)
    if not rows:
        raise ValueError('候选角色目录没有正式主动技能')
    requests, mappings = {}, {}
    for identity, asset_path in rows:
        if not isinstance(asset_path, str) or not asset_path.startswith('/Game/'):
            raise ValueError(f'正式技能缺少游戏图标路径：{identity}')
        package = asset_path.split('.', 1)[0]
        original = (source/(package.removeprefix('/Game/')+'.png')).resolve()
        if source not in original.parents:
            raise ValueError('技能图片来源越过 Content 目录')
        relative = 'skills/'+original.name
        target = (output/relative).resolve()
        if output not in target.parents:
            raise ValueError('技能图片输出越界')
        if relative in requests and requests[relative][0] != original:
            raise ValueError('不同来源技能图片输出名称冲突')
        # Validate every source before writing any new file or replacing the manifest.
        with Image.open(original) as image:
            if image.format != 'PNG' or image.width*image.height > 24_000_000:
                raise ValueError('技能来源图片类型或大小无效')
            image.load()
            size = image.size
        requests[relative] = (original, asset_path, size)
        mappings[str(identity)] = relative
    previous = set(manifest.get('skills', {}).values())
    obsolete = previous-set(requests)
    other_references = {value for group, mapping in manifest.items()
                        if group not in {'files', 'skills'} and isinstance(mapping, dict)
                        for value in mapping.values() if isinstance(value, str)}
    if obsolete & other_references:
        raise ValueError('旧技能图片仍被其他资源组引用')
    for relative in obsolete:
        target = (output/relative).resolve()
        if output not in target.parents or not relative.startswith('skills/'):
            raise ValueError('旧技能图片清理越界')
    for relative, (original, asset_path, size) in requests.items():
        target = output/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        digest = hashlib.sha256(original.read_bytes()).hexdigest().upper()
        if hashlib.sha256(target.read_bytes()).hexdigest().upper() != digest:
            raise ValueError('技能图片复制哈希不一致')
        manifest['files'][relative] = {
            'source_asset_path': asset_path, 'source_sha256': digest, 'sha256': digest,
            'width': size[0], 'height': size[1], 'bytes': target.stat().st_size,
        }
    for relative in obsolete:
        (output/relative).unlink(missing_ok=True)
        manifest['files'].pop(relative, None)
    manifest['skills'] = mappings
    manifest['total_files'] = len(manifest['files'])
    manifest['total_bytes'] = sum((output/name).stat().st_size for name in manifest['files'])
    temporary = path.with_suffix('.json.pending')
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    temporary.replace(path)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--candidate-dir', type=Path, required=True)
    parser.add_argument('--target-dir', type=Path, help='仅技能图片维护时，经正式目录晋升 helper 安装整包')
    args = parser.parse_args()
    candidate = args.candidate_dir.resolve()
    if ROOT/'build' not in candidate.parents:
        parser.error('技能图片只生成到 build 候选目录')
    manifest = add_role_skill_assets(args.source, candidate/'game_static.sqlite3', candidate/'game_ui')
    print(json.dumps({'skill_ids': len(manifest['skills']), 'skill_images': len(set(manifest['skills'].values()))}))
    if args.target_dir is not None:
        from tools.game_data.promote_role_catalog import promote_role_skill_assets
        print(json.dumps(promote_role_skill_assets(candidate, args.target_dir), ensure_ascii=False))


if __name__ == '__main__':
    main()
