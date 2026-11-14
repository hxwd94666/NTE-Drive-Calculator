# 按维护者明确选择记录可延期的角色头像和立绘，不补造图片或清空来源缺项。
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.integrations.game_assets.deferred_images import validate_deferred_images
from src.integrations.static_catalog.role_catalog_release import read_role_catalog
from tools.game_data.build_reference_catalog_assets import reference_recipe


def defer_images(candidate: Path, source: Path, selections: list[str]) -> dict:
    candidate, source = candidate.resolve(), source.resolve()
    if ROOT / 'build' not in candidate.parents:
        raise ValueError('延期确认只写 build 内候选')
    path = candidate / 'game_ui/manifest.json'
    original = path.read_bytes()
    manifest = json.loads(original)
    missing = manifest.get('unresolved_assets', [])
    if not selections or len(set(selections)) != len(selections):
        raise ValueError('逐项指定不重复的 group:character_id')
    if set(selections) != {f"{r['group']}:{r['id']}" for r in missing}:
        raise ValueError('延期选择必须精确覆盖本次缺项，新增缺项须重新确认')
    recipe = reference_recipe(source, candidate / 'game_static.sqlite3')
    bindings = {(group, str(row['character_id'])): row['source_asset_path']
                for group in ('characters', 'character_arts') for row in recipe[group]}
    for row in missing:
        reference = bindings.get((row['group'], row['id']))
        if not isinstance(reference, str) or not reference.startswith('/Game/'):
            raise ValueError('延期图片缺少同版正式资源绑定')
        expected = reference.split('.', 1)[0].removeprefix('/Game/') + '.png'
        if expected != row['source'] or row['reason'] != 'missing_same_version_source':
            raise ValueError('延期图片来源与当前正式绑定不一致')
        resolved = (source / expected).resolve()
        if source not in resolved.parents or resolved.exists():
            raise ValueError('延期图片已存在或路径越界，应重建素材而非延期')
    manifest['deferred_optional_assets'] = missing
    validate_deferred_images(manifest)
    temporary = path.with_suffix('.json.pending')
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
    try:
        release = read_role_catalog(candidate)
    except BaseException:
        temporary.write_bytes(original)
        temporary.replace(path)
        raise
    return {'dataset_id': release.dataset_id, 'database_sha256': release.sha256,
            'deferred_optional_assets': manifest['deferred_optional_assets'],
            'unresolved_assets_retained': True,
            'image_manifest_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-dir', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--defer-character-image', action='append', required=True)
    args = parser.parse_args()
    print(json.dumps(defer_images(args.candidate_dir, args.source, args.defer_character_image), ensure_ascii=False))


if __name__ == '__main__':
    main()
