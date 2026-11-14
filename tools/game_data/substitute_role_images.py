# 将维护者选定的同版人物图片原样纳入隔离候选，保留缺失默认图及临时用途说明。
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.integrations.static_catalog.role_catalog_release import read_role_catalog
from tools.game_assets.build_ui_assets import _source_file


def substitute_images(candidate: Path, source: Path, character_id: int, selections: dict) -> dict:
    candidate, source = candidate.resolve(), source.resolve()
    if ROOT / 'build' not in candidate.parents:
        raise ValueError('临时图片只写 build 内隔离候选')
    release = read_role_catalog(candidate)
    path = release.asset_root / 'manifest.json'
    manifest = json.loads(path.read_text('utf-8'))
    missing = {(row['group'], row['id']): row for row in manifest.get('unresolved_assets', [])}
    manifest.setdefault('temporary_substitutions', {})
    for group, (reference, notice) in selections.items():
        row = missing.get((group, str(character_id)))
        if group not in {'characters', 'character_arts'} or row is None:
            raise ValueError('临时图片仅适用于已登记缺少默认图片的正式角色')
        original = (source / row['source']).resolve()
        if source not in original.parents or original.exists():
            raise ValueError('默认图片已经存在或路径越界，应重建正式素材并结束临时替代')
        selected = _source_file(source, reference)
        with Image.open(selected) as image:
            if image.format != 'PNG' or image.width * image.height > 24_000_000:
                raise ValueError('临时来源必须是合理大小的 PNG')
            image.load()
            width, height = image.size
        relative = f'temporary/{group}/{character_id}.png'
        target = release.asset_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(selected, target)
        digest = hashlib.sha256(selected.read_bytes()).hexdigest().upper()
        assert hashlib.sha256(target.read_bytes()).hexdigest().upper() == digest
        manifest[group][str(character_id)] = relative
        manifest['files'][relative] = {'sha256': digest, 'source_sha256': digest,
            'source_asset_path': reference, 'width': width, 'height': height, 'bytes': target.stat().st_size}
        manifest['temporary_substitutions'][f'{group}:{character_id}'] = {
            'original_source': row['source'], 'source_asset_path': reference,
            'source_sha256': digest, 'notice': notice}
    manifest['total_files'] = len(manifest['files'])
    manifest['total_bytes'] = sum((release.asset_root / name).stat().st_size for name in manifest['files'])
    pending = path.with_suffix('.json.pending')
    pending.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    pending.replace(path)
    read_role_catalog(candidate)
    return {'dataset_id': release.dataset_id, 'database_sha256': release.sha256,
            'temporary_substitutions': manifest['temporary_substitutions'], 'source_pixels_modified': False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-dir', required=True, type=Path)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--character-id', required=True, type=int)
    parser.add_argument('--avatar-source', required=True)
    parser.add_argument('--art-source', required=True)
    parser.add_argument('--avatar-notice', required=True)
    parser.add_argument('--art-notice', required=True)
    args = parser.parse_args()
    result = substitute_images(args.candidate_dir, args.source, args.character_id, {
        'characters': (args.avatar_source, args.avatar_notice), 'character_arts': (args.art_source, args.art_notice)})
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
