# 三种倒带策略的纯值结构：旧值同源初始化一次，草稿、执行选项和方案分别冻结。
from __future__ import annotations

from copy import deepcopy

STRATEGIES = ('balanced', 'focused', 'dual_target')
PROFILE_VERSION = 1
PROFILE_PREFIX = 'rewind_strategy_'
UI_KEY = 'rewind_strategy_ui'
PLAN_FIELDS = frozenset({'saved_rewind_shape_ids', 'saved_rewind_slots', 'plan_origin',
                         'saved_guard', 'saved_request', 'saved_inputs', 'saved_at_utc'})
EXECUTION_FIELDS = frozenset({'rewind_qualities', 'rewind_drive_customization', 'rewind_ten_limits'})
META_FIELDS = frozenset({'strategy', '_revision'})


def strategy_key(strategy):
    if strategy not in STRATEGIES:
        raise ValueError('invalid_rewind_strategy')
    return PROFILE_PREFIX + strategy


def split_profile(value, *, revision=0, seed='edited'):
    """深拷贝每个字段；未提供的事实不补成已计算结果或零分。"""
    return dict(version=PROFILE_VERSION, revision=revision, seed=seed,
        draft={k: deepcopy(v) for k, v in value.items() if k not in PLAN_FIELDS | EXECUTION_FIELDS | META_FIELDS},
        execution_options={k: deepcopy(v) for k, v in value.items() if k in EXECUTION_FIELDS},
        saved_plan={k: deepcopy(v) for k, v in value.items() if k in PLAN_FIELDS})


def flatten_profile(profile, strategy):
    strategy_key(strategy)
    if (not isinstance(profile, dict) or type(profile.get('version')) is not int or profile.get('version') != PROFILE_VERSION
            or type(profile.get('revision')) is not int or profile['revision'] < 0
            or any(not isinstance(profile.get(k), dict) for k in ('draft', 'execution_options', 'saved_plan'))):
        raise ValueError('invalid_rewind_profile')
    return {**deepcopy(profile['draft']), **deepcopy(profile['execution_options']),
            **deepcopy(profile['saved_plan']), 'strategy': strategy, '_revision': profile['revision']}


def initial_profiles(legacy=None, dual=None):
    """完整旧配置成为三份相同初值；保留两类名单与已有双组，不靠最后打开的页猜生成归属。"""
    legacy, dual = deepcopy({} if legacy is None else legacy), deepcopy({} if dual is None else dual)
    if not isinstance(legacy, dict) or not isinstance(dual, dict):
        raise ValueError('invalid_rewind_legacy')
    seed = {**legacy, **{k: v for k, v in dual.items() if k not in PLAN_FIELDS}}
    # 原共享八槽优先；只有原共享方案确实不存在时才采用旧兼顾方案，不无声丢弃唯一旧方案。
    if not legacy.get('saved_rewind_shape_ids') and dual.get('saved_rewind_shape_ids'):
        seed.update({k: v for k, v in dual.items() if k in PLAN_FIELDS})
    if seed.get('saved_rewind_shape_ids'):
        seed.setdefault('plan_origin', 'legacy')
    if 'groups' not in seed:
        maps = legacy.get('selected_slots_by_strategy', {})
        maps = maps if isinstance(maps, dict) else {}
        selected = legacy.get('selected_slots', {})
        selected = selected if isinstance(selected, dict) else {}
        target = {str(i) for i in legacy.get('target_character_ids', ())}
        primary = {str(i) for i in legacy.get('main_character_ids', ())}
        # 原名单允许交叠，双组不允许；冲分名单保留在高分组，其余培养名单进入低分组。
        # 原始两份完整名单仍原样保留在每份草稿中，不删除角色或伪造槽位。
        seed['groups'] = {key: dict(slots={i: (maps.get(mode) or selected).get(i) for i in sorted(ids)},
            grade=legacy.get('target_grade', default), mode=legacy.get('target_threshold_mode', 'grade'),
            custom_percent=legacy.get('target_custom_percent'))
            for key, mode, ids, default in [('improve', 'balanced', target - primary, 'A'),
                                            ('push', 'focused', primary, 'SSS')]}
    profile = split_profile(seed, seed='legacy' if legacy or dual else 'empty')
    return {strategy: deepcopy(profile) for strategy in STRATEGIES}


TEN_LIMIT_QUALITIES = ("blue", "purple", "gold")
TEN_LIMIT_MAXIMUM = 9999


def parse_ten_limits(value) -> tuple[tuple[str, int], ...]:
    """读取账号偏好中的 {品质: 次数}；损坏、非正整数或未知品质按“不填”处理，按蓝紫金顺序冻结。"""
    if not isinstance(value, dict):
        return ()
    return tuple((quality, value[quality]) for quality in TEN_LIMIT_QUALITIES
                 if type(value.get(quality)) is int and 0 < value[quality] <= TEN_LIMIT_MAXIMUM)
