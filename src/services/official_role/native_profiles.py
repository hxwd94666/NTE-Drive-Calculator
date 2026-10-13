# 按正式静态身份投影稀疏角色字段并筛选独立字段组，未知不补零且保存仍归原事务。
from __future__ import annotations
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_support import UserDataValidationError, _valid_breakthrough_stage_for_level
from collections import Counter
from collections.abc import Sequence
from contextlib import nullcontext
from src.storage.sqlite.characters.native_character_profile_dao import normalize_native_profile_patches


def _latest_growth_values(rows):
    """调用方保留各自缺项报错；这里只复用同一最大等级/突破行的纯值投影。"""
    growth = max(rows, key=lambda row: (int(row["level"]), int(row["breakthrough_stage"])))
    return {"character_level": int(growth["level"]), "breakthrough_stage": int(growth["breakthrough_stage"])}


def load_template_growth_defaults(
    character_ids: tuple[int, ...], *, static_database_path: str | Path,
) -> dict[int, dict[str, int]]:
    defaults = {}
    if not character_ids:
        return defaults
    with StaticGameDataDao(static_database_path) as dao:
        for character_id in character_ids:
            if dao.get_character(character_id) is None:
                raise UserDataValidationError("角色状态包含当前正式静态目录中不存在的角色")
            rows = dao.list_character_panel_growth(character_id)
            if not rows:
                raise UserDataValidationError("正式角色模板缺少成长数据，无法核对稀疏角色状态")
            defaults[character_id] = _latest_growth_values(rows)
    return defaults


def project_native_role_profile(
    base: Mapping[str, Any], observation: Mapping[str, Any] | None, *, persisted: bool,
) -> dict[str, Any]:
    profile = dict(base)
    sources = {name: "account" if persisted else "template" for name in base
               if name not in {"persisted", "field_sources", "created_at_utc", "updated_at_utc"}}
    # 稀疏原生只覆盖已证字段；未返回的账号/模板值及逐字段来源仍保留。
    for name in ("character_level", "breakthrough_stage"):
        value = (observation or {}).get(name)
        if value is not None:
            profile[name] = value
            sources[name] = "native_observed"
    observation = observation or {}
    if observation.get("awakening_level") is not None:
        profile["awakening_level"] = observation["awakening_level"]
        sources["awakening_level"] = "native_observed"
    if observation.get("awakening_selection_initialized") is True:
        profile["selected_awaken_effect_ids"] = list(observation["selected_awaken_effect_ids"])
        profile["awakening_selection_initialized"] = True
        sources["selected_awaken_effect_ids"] = "native_observed"
        sources["awakening_selection_initialized"] = "native_observed"
    for name in ("likeability_level", "likeability_level_10_enabled", "fork_id", "fork_level", "fork_breakthrough_stage", "fork_refinement_level"):
        if name in observation and (not name.startswith("fork_") or observation.get("fork_observed") is True):
            profile[name] = observation[name]
            sources[name] = "native_observed"
    if observation.get("skill_levels"):
        profile["skill_levels"] = {**profile.get("skill_levels", {}), **observation["skill_levels"]}
        sources["skill_levels"] = "native_observed"
    if observation and not _valid_breakthrough_stage_for_level(profile["character_level"], profile["breakthrough_stage"]):
        raise UserDataValidationError("角色原生观测与当前模板的等级、突破组合不匹配")
    profile["field_sources"] = sources
    return profile


def validate_native_cultivation(patches, *, static_database_path):
    """写入前核对本次冻结正式目录中的身份，不借显示名称补齐缺项。"""
    if not any(patch.get("skill_levels") or patch.get("fork_observed") or "likeability_levels" in patch
               or "likeability_level" in patch or "likeability_level_10_enabled" in patch
               or patch.get("awakening_selection_initialized") for patch in patches):
        return
    with StaticGameDataDao(static_database_path) as dao:
        fork_ids = {row["fork_id"] for row in dao.list_forks()} if any(patch.get("fork_id") for patch in patches) else set()
        for patch in patches:
            validate_native_cultivation_patch(patch, dao, fork_ids)


def validate_native_cultivation_patch(patch, dao, fork_ids):
    """核对一个独立字段组；批量严格校验与角色稀疏同步共用此规则。"""
    resolve_native_likeability(patch, dao)
    if patch.get("awakening_selection_initialized"):
        effects = {row["effect_id"] for row in dao.list_character_awaken_effects(patch["character_id"])
                   if row["awaken_type"] == "Awaken_Effect"}
        if any(value not in effects for value in patch["selected_awaken_effect_ids"]):
            raise UserDataValidationError("原生觉醒效果不在当前角色的官方目录中")
    # 成长行表示从该级升到下一级的消耗；最高基础技能等级为最后消耗行等级 +1。
    skills = {row["skill_id"]: max((int(level["level"]) for level in row["levels"]), default=0) + 1
              for row in dao.list_character_skills(patch["character_id"])} if patch.get("skill_levels") else {}
    if any(key not in skills or not 1 <= level <= skills[key] for key, level in patch.get("skill_levels", {}).items()):
        raise UserDataValidationError("原生技能身份或基础等级不在当前官方目录中")
    fork_id = patch.get("fork_id")
    if patch.get("fork_observed") and fork_id is not None:
        if fork_id not in fork_ids:
            matches = [identity for identity in fork_ids if identity.casefold() == fork_id.casefold()]
            if not matches:
                raise UserDataValidationError("原生弧盘身份不在当前官方目录中")
            if len(matches) != 1:
                raise UserDataValidationError("原生弧盘身份存在大小写匹配冲突，未采用该弧盘状态")
            # 仅纠正唯一正式身份的字母大小写，不补前缀或改写原始采集记录。
            patch["fork_id"] = matches[0]
    if "likeability_level_10_enabled" in patch:
        bonus = dao.get_character_likeability_bonus(patch["character_id"])
        if bonus is None:
            patch["likeability_level_10_enabled"] = False
        elif int(bonus["required_level"]) != 10:
            raise UserDataValidationError("原生好感度门槛与当前官方目录不匹配")


def resolve_native_likeability(profile, static_dao):
    """仅通过正式好感度集合身份解析观测；缺映射保留未知而非补零。"""
    levels = profile.pop("likeability_levels", None)
    if levels is None:
        return
    identity = static_dao.get_character_likeability_identity(profile["character_id"])
    if identity is None or identity["likeability_id"] not in levels:
        return
    level = levels[identity["likeability_id"]]
    profile["likeability_level"] = level
    profile["likeability_level_10_enabled"] = level >= int(identity["required_level"])


def _field_groups(row):
    for name, label in (('character_level', '等级'), ('breakthrough_stage', '突破'),
                        ('awakening_level', '觉醒等级')):
        if name in row:
            yield label, {name: row[name]}
    for label, names in (
        ('觉醒选择', ('awakening_level', 'awakening_selection_initialized', 'selected_awaken_effect_ids')),
        ('好感度', ('likeability_levels', 'likeability_level', 'likeability_level_10_enabled')),
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
    # 重复身份整角色排除；字段组失败只跳该组，原事务 owner 决定整体提交。
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
                    growth = _latest_growth_values(rows)
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
