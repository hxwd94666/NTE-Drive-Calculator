# 校验逐项确认延期的角色展示图片；保留缺项，不放宽技能、数值或已有文件校验。
from __future__ import annotations

from pathlib import PurePosixPath
import re


def _validate_substitute(manifest, row, substitute):
    """临时图同时绑定缺失原图、选定来源和实际输出，不充当外观/养成证据。"""
    if not isinstance(substitute, dict) or set(substitute) != {'original_source', 'source_asset_path', 'source_sha256', 'notice'}:
        raise ValueError('临时替代图片元数据无效')
    if substitute['original_source'] != row['source']:
        raise ValueError('临时替代图片的缺失原图不一致')
    source, digest, notice = (substitute[key] for key in ('source_asset_path', 'source_sha256', 'notice'))
    if not isinstance(source, str) or not source.startswith('/Game/') or '\\' in source or '..' in source.split('/'):
        raise ValueError('临时替代图片来源路径无效')
    if not isinstance(digest, str) or not re.fullmatch('[0-9A-F]{64}', digest):
        raise ValueError('临时替代图片来源哈希无效')
    if not isinstance(notice, str) or not notice.startswith('临时替代') or len(notice) > 300:
        raise ValueError('临时替代图片须保留可见说明')
    relative = manifest.get(row['group'], {}).get(row['id'])
    if relative != f"temporary/{row['group']}/{row['id']}.png":
        raise ValueError('临时图片必须使用独立输出路径')
    metadata = manifest.get('files', {}).get(relative, {})
    if metadata.get('source_asset_path') != source or metadata.get('source_sha256') != digest or metadata.get('sha256') != digest:
        raise ValueError('临时图片输出与原图来源不一致')


def validate_deferred_images(manifest: dict) -> tuple[int, ...]:
    """延期与缺项须一一对应，仅头像/立绘可延期，普通残缺包仍拒绝。"""
    missing = manifest.get('unresolved_assets', [])
    accepted = manifest.get('deferred_optional_assets', [])
    substitutes = manifest.get('temporary_substitutions', {})
    if not isinstance(substitutes, dict) or len(substitutes) > 64:
        raise ValueError('临时替代图片清单无效')
    if not isinstance(missing, list) or not isinstance(accepted, list) or len(missing) > 64:
        raise ValueError('角色图片缺项清单无效')
    if not missing and not accepted:
        if substitutes:
            raise ValueError('正式图片已齐全，临时替代记录应移除')
        return ()
    if not missing or len(missing) != len(accepted):
        raise ValueError('角色图片来源尚未完整解析：延期确认与缺项不一致')
    seen, identities, used = set(), set(), set()
    for row in missing:
        if not isinstance(row, dict) or set(row) != {'group', 'id', 'source', 'reason'}:
            raise ValueError('延期图片缺少正式来源或原因')
        group, identity, source = row['group'], row['id'], row['source']
        if group not in {'characters', 'character_arts'} or row['reason'] != 'missing_same_version_source':
            raise ValueError('仅缺少同版来源的角色头像和立绘可延期')
        if not isinstance(identity, str) or not identity.isascii() or not identity.isdecimal() or not 0 < int(identity) < 2**31:
            raise ValueError('延期图片必须使用正式角色数字身份')
        if not isinstance(source, str) or '\\' in source:
            raise ValueError('延期图片来源路径无效')
        path = PurePosixPath(source)
        if path.is_absolute() or '..' in path.parts or source != path.as_posix() or path.suffix != '.png':
            raise ValueError('延期图片来源越界或类型错误')
        if not source.startswith(f'UI/UI_Icon/Appearance/{identity}/'):
            raise ValueError('延期图片来源与角色身份不一致')
        key = (group, identity)
        if key in seen:
            raise ValueError('延期图片重复')
        substitute_key = f'{group}:{identity}'
        if identity in manifest.get(group, {}):
            _validate_substitute(manifest, row, substitutes.get(substitute_key))
            used.add(substitute_key)
        seen.add(key)
        identities.add(int(identity))
    # 不接受笼统开关，也不忽略清单内新增的未确认缺项。
    if any(not isinstance(row, dict) for row in accepted) or sorted(missing, key=lambda r: (r['group'], r['id'])) != sorted(accepted, key=lambda r: (r.get('group', ''), r.get('id', ''))):
        raise ValueError('角色图片来源尚未完整解析：存在未确认延期的缺项')
    if set(substitutes) != used:
        raise ValueError('临时替代记录缺少对应角色图片映射')
    return tuple(sorted(identities))
