# 按独立字段组筛选原生角色观测，汇总错误并保留可安全保存的数据。
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import nullcontext

from src.storage.sqlite.native_character_profile_dao import normalize_native_profile_patches
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_support import UserDataValidationError
from src.services.native_role_profile_projection import validate_native_cultivation_patch


def _field_groups(row):
    for name, label in (('character_level', '等级'), ('breakthrough_stage', '突破'),
                        ('awakening_level', '觉醒等级')):
        if name in row:
            yield label, {name: row[name]}
    for label, names in (
        ('觉醒选择', ('awakening_level', 'awakening_selection_initialized', 'selected_awaken_effect_ids')),
        ('好感度', ('likeability_levels', 'likeability_level_10_enabled')),
        ('弧盘', ('fork_observed', 'fork_id', 'fork_level', 'fork_breakthrough_stage', 'fork_refinement_level')),
    ):
        if label == '觉醒选择' and 'awakening_selection_initialized' not in row:
            continue
        group = {name: row[name] for name in names if name in row}
        if group:
            identity = group.get('fork_id')
            if label == '弧盘' and isinstance(identity, str) and len(identity) <= 128:
                label += f' {identity}'
            yield label, group
    skills = row.get('skill_levels')
    if isinstance(skills, Mapping) and len(skills) <= 64:
        for skill_id, level in skills.items():
            label = f'技能 {skill_id}' if isinstance(skill_id, str) and len(skill_id) <= 128 else '技能'
            yield label, {'skill_levels': {skill_id: level}}
    elif skills is not None:
        yield '技能', {'skill_levels': skills}


def prepare_native_role_patches(profiles, *, static_database_path, growth_defaults_loader):
    if isinstance(profiles, (str, bytes)) or not isinstance(profiles, Sequence):
        raise UserDataValidationError('正式角色状态必须是角色列表')
    counts = Counter(row['character_id'] for row in profiles if isinstance(row, Mapping)
                     and type(row.get('character_id')) is int)
    patches, defaults, warnings = [], {}, []
    context = StaticGameDataDao(static_database_path) if static_database_path is not None else nullcontext()
    with context as static:
        fork_ids = {row['fork_id'] for row in static.list_forks()} if static is not None else set()
        for index, row in enumerate(profiles, 1):
            character_id = row.get('character_id') if isinstance(row, Mapping) else None
            if type(character_id) is not int or character_id <= 0:
                warnings.append(f'第 {index} 条角色记录：缺少有效的正式角色身份。')
                continue
            label = f'角色 {character_id}'
            if counts[character_id] > 1:
                warning = f'{label}：存在重复角色身份，未采用该角色记录。'
                if warning not in warnings:
                    warnings.append(warning)
                continue
            if static is not None:
                character = static.get_character(character_id)
                if character is None:
                    warnings.append(f'{label}：不在当前官方角色目录中。')
                    continue
                label = f"{character.get('name_zh') or '角色'}（{character_id}）"
            try:
                if growth_defaults_loader is not None:
                    growth = growth_defaults_loader((character_id,))[character_id]
                elif static is not None:
                    rows = static.list_character_panel_growth(character_id)
                    if not rows:
                        raise UserDataValidationError('正式角色模板缺少成长数据')
                    latest = max(rows, key=lambda value: (int(value['level']), int(value['breakthrough_stage'])))
                    growth = {'character_level': int(latest['level']),
                              'breakthrough_stage': int(latest['breakthrough_stage'])}
                else:
                    raise ValueError('角色状态同步缺少本次冻结的静态数据库路径')
            except UserDataValidationError as error:
                warnings.append(f'{label}：{error}')
                continue
            patch = {'character_id': character_id}
            for field_label, group in _field_groups(row):
                try:
                    normalized = normalize_native_profile_patches([{'character_id': character_id, **group}])
                    if not normalized:
                        continue
                    candidate = normalized[0]
                    if static is not None:
                        validate_native_cultivation_patch(candidate, static, fork_ids)
                except UserDataValidationError as error:
                    warnings.append(f'{label} · {field_label}：{error}')
                    continue
                if 'skill_levels' in candidate:
                    patch.setdefault('skill_levels', {}).update(candidate.pop('skill_levels'))
                patch.update(candidate)
            if len(patch) > 1:
                patches.append(patch)
                defaults[character_id] = growth
    return patches, defaults, tuple(warnings)
