# 按工作模式筛选账号练度角色，并在模板投影前排除全未知占位记录。
from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from concurrent.futures import CancelledError
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.domain.allocation_rating import loadout_total_grade
from src.domain.loadout_plan_scores import accumulate_assignment_scores
from src.domain.loadout_suit_activation import active_set_count
from src.domain.practice_share import PracticeEntry, PracticeSharePanel, PracticeValue, sort_practice_entries
from src.domain.work_mode import WorkMode
from src.integrations.loadout_share_assets import share_asset
from src.optimizer.contracts import EQUIP_SCORE, EQUIP_UID, ROLE_EQUIPPED_DRIVES, ROLE_EQUIPPED_TAPE, ROLE_TOTAL_SCORE
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.official_role_attribute_service import _compatible_forks
from src.services.official_role_profile_projection import resolve_official_role_profile
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao

ELEMENT_PROPERTY = {
    'CHAOS': 'DamageUpChaosBase', 'COSMOS': 'DamageUpCosmosBase',
    'INCANTATION': 'DamageUpIncantationBase', 'LAKSHANA': 'DamageUpLakshanaBase',
    'NATURE': 'DamageUpNatureBase', 'PSYCHE': 'DamageUpPsycheBase',
}


@dataclass(frozen=True, slots=True)
class PracticeShareRequest:
    user_database_path: Path
    static_database_path: Path
    asset_root: Path
    states: tuple[Mapping[str, Any], ...]
    work_mode: WorkMode = WorkMode.OFFLINE


def _check(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise CancelledError()


def _equipped_character_ids(states: Mapping[int, Mapping]) -> set[int]:
    """Only actual equipped game projections define the exported roster, not historical mappings."""
    return {
        identity for identity, state in states.items()
        if any(isinstance(item, Mapping) and item.get('kind') in {'core', 'module'}
               for item in getattr(state.get('_game_projection'), 'items', ()))
    }


def _has_known_cultivation(record: Mapping) -> bool:
    """IDs, timestamps, empty containers and absence flags are not displayed cultivation."""
    for key, low, high in (('character_level', 1, 80), ('awakening_level', 0, 6),
                           ('likeability_level', 0, 100)):
        value = record.get(key)
        if type(value) is int and low <= value <= high:
            return True
    if any(type(value) is int and 1 <= value <= 15 for value in (record.get('skill_levels') or {}).values()):
        return True
    return bool(record.get('fork_id'))


def _practice_character_ids(mode: WorkMode, context: Mapping, states: Mapping[int, Mapping]) -> set[int]:
    equipped = _equipped_character_ids(states)
    if mode not in {WorkMode.MEDIUM, WorkMode.DEVELOPER}:
        return equipped
    native = {int(row['character_id']): row for row in context['observations']}
    profiles = {int(row['character_id']): row for row in context['profiles']}
    # Manual role saves clear native overrides; an observed identity plus saved cultivation remains valid.
    observed = set(context.get('observed_ids') or ())
    known = {identity for identity, record in native.items() if _has_known_cultivation(record)}
    known.update(identity for identity in observed if identity not in native and
                 _has_known_cultivation(profiles.get(identity, {})))
    return equipped | known


def _entry_has_known_data(entry: PracticeEntry) -> bool:
    values = (entry.level, entry.awakening, entry.heart, *entry.skills, entry.fork_refinement)
    return any(value.text not in {'', '—'} for value in values) or bool(entry.fork_name) or entry.score is not None


def _profile_value(profile: Mapping, key: str) -> PracticeValue:
    value = profile.get(key)
    configured = (profile.get('field_sources') or {}).get(key) != 'native_observed'
    return PracticeValue(str(value), configured) if value is not None else PracticeValue()


def _heart_value(profile: Mapping) -> PracticeValue:
    exact = profile.get('likeability_level')
    return _profile_value(profile, 'likeability_level') if type(exact) is int and 0 <= exact <= 100 else PracticeValue()


def _skill_values(profile: Mapping, skills: list[dict]) -> tuple[PracticeValue, ...]:
    # Official ability_index owns 普/技/终/连 order; no text guessing or awakening-added levels.
    ordered = sorted((row for row in skills if str(row.get('ability_type') or '').casefold() == 'proactive'),
                     key=lambda row: (int(row.get('ability_index', 0)), str(row['skill_id'])))[:4]
    values = []
    for skill in ordered:
        key = str(skill['skill_id'])
        level = (profile.get('skill_levels') or {}).get(key)
        configured = (profile.get('field_sources') or {}).get('skill_levels') != 'native_observed'
        values.append(PracticeValue(str(level), configured) if level is not None else PracticeValue())
    return tuple(values + [PracticeValue()] * (4 - len(values)))


def _loadout_score(state: Mapping) -> float | None:
    if not state.get('_game_mode') or not state.get('_game_importable'):
        return None
    items = ([state[ROLE_EQUIPPED_TAPE]] if state.get(ROLE_EQUIPPED_TAPE) else []) + list(state.get(ROLE_EQUIPPED_DRIVES) or ())
    if not items or any(not isinstance(item, Mapping) or item.get(EQUIP_SCORE) is None or
                        not item.get(EQUIP_UID) or item.get('virtual') for item in items):
        return None
    identities = [str(item[EQUIP_UID]) for item in items]
    if len(set(identities)) != len(identities):
        return None
    scores = [float(item[EQUIP_SCORE]) for item in items]
    total = state.get(ROLE_TOTAL_SCORE)
    if total is None or not all(math.isfinite(value) for value in scores):
        return None
    total = float(total)
    if not math.isfinite(total) or not math.isclose(total, accumulate_assignment_scores(scores), abs_tol=1e-6):
        return None
    return total


def build_practice_share_panel(
    request: PracticeShareRequest, *, cancelled: Callable[[], bool] = lambda: False,
) -> PracticeSharePanel:
    _check(cancelled)
    if any(not state.get('_game_mode') for state in request.states):
        raise ValueError('练度统计只消费游戏配装，不混入计算配装')
    states = {int(row['_character_id']): row for row in request.states if row.get('_character_id') is not None}
    if len(states) != sum(row.get('_character_id') is not None for row in request.states):
        raise ValueError('游戏配装角色身份重复')
    mode = WorkMode(request.work_mode)
    full_roster = mode in {WorkMode.MEDIUM, WorkMode.DEVELOPER}
    with UserDataDao(request.user_database_path) as dao:
        context = dao.read_practice_share_context(tuple(sorted(_equipped_character_ids(states))),
                                                include_unequipped=full_roster)
    _check(cancelled)
    identities = _practice_character_ids(mode, context, states)
    if not identities:
        raise ValueError('当前账号暂无符合统计范围的已知角色数据')
    profiles = {int(row['character_id']): row for row in context['profiles']}
    native = {int(row['character_id']): row for row in context['observations']}
    catalog = GameUiAssetCatalog(request.asset_root)
    entries = []
    with StaticGameDataDao(request.static_database_path) as static:
        characters = {int(row['character_id']): row for row in static.list_characters()}
        forks = {str(row['fork_id']): row for row in static.list_forks()}
        fork_templates = static.list_fork_templates()
        suits = {str(row['suit_id']): row for row in static.list_suits()}
        core_suits = {str(row['item_id']): str(row['suit_id'] or '') for row in static.list_equipment_items('core')}
        shapes = frozenset(str(row['shape_id']) for row in static.list_shapes())
        for identity in sorted(identities):
            _check(cancelled)
            character = characters.get(identity)
            if character is None:
                continue  # Custom roles aren't official observed game characters.
            skills = static.list_character_skills(identity)
            profile = resolve_official_role_profile(
                character, static.list_character_panel_growth(identity),
                _compatible_forks(character, fork_templates), skills, static.list_character_awaken_effects(identity),
                saved_profile=profiles.get(identity), observation=native.get(identity),
                likeability_bonus=static.get_character_likeability_bonus(identity),
            )
            if full_roster and identity not in profiles:
                # Medium/developer observed rows must not turn missing cultivation into default level 80.
                profile = {key: value for key, value in profile.items()
                           if (profile.get('field_sources') or {}).get(key) != 'template'}
                if native.get(identity, {}).get('skill_levels'):
                    profile['skill_levels'] = dict(native[identity]['skill_levels'])
            state = states.get(identity, {})
            fork_value = _profile_value(profile, 'fork_id')
            fork_id = '' if fork_value.text == '—' else fork_value.text
            fork = forks.get(fork_id, {})
            score = _loadout_score(state)
            entry = PracticeEntry(
                character_id=identity, name=str(character.get('name_zh') or identity),
                level=_profile_value(profile, 'character_level'),
                awakening=_profile_value(profile, 'awakening_level'), heart=_heart_value(profile),
                skills=_skill_values(profile, skills),
                avatar=catalog.character_icon(identity), element_icon=share_asset('properties', ELEMENT_PROPERTY.get(
                    str(character.get('element_type') or '').rsplit('_TYPE_', 1)[-1], '')),
                fork_name=str(fork.get('name_zh') or fork_id),
                fork_refinement=_profile_value(profile, 'fork_refinement_level') if fork_id else PracticeValue(),
                fork_icon=catalog.fork_icon(fork_id) if fork_id else None,
                score=score, grade=loadout_total_grade(score) if score is not None else '',
                active_set_count=active_set_count(getattr(state.get('_game_projection'), 'items', None),
                                                  suits, core_suits, shapes),
            )
            if _entry_has_known_data(entry):
                entries.append(entry)
    if not entries:
        raise ValueError('当前账号暂无非全未知的正式角色统计数据')
    scope = ('中风险／开发模式：展示有真实账号养成或游戏配装的角色，未装备角色保留；全未知占位记录不展示。'
             if full_roster else '低风险／离线模式：仅统计已知有游戏配装的角色；养成读取账号配置，无记录时使用模板。')
    return PracticeSharePanel(sort_practice_entries(tuple(entries)), notes=(scope, *PracticeSharePanel(()).notes))
