# UI 合成服务使用与正式接口相同的三策略修订及草稿/方案分区，不连接真实账号。
from copy import deepcopy
from inspect import Parameter, signature

from src.domain.rewind.strategy_preferences import PLAN_FIELDS, STRATEGIES, flatten_profile, initial_profiles
from src.services.rewind.dual_recommendation import guard_payload


class ProfileServiceFixture:
    def __new__(cls, inner):
        if isinstance(inner, cls):
            return inner
        previous = getattr(inner, '_profile_service_fixture', None)
        if previous is not None:
            return previous
        value = super().__new__(cls)
        inner._profile_service_fixture = value
        return value

    def __init__(self, inner):
        if 'inner' in self.__dict__:
            return
        self.inner = inner
        seed = getattr(inner, 'load_preferences', lambda: {})()
        dual = getattr(inner, 'load_dual_target_preferences', lambda: {})()
        self.profiles = {k: flatten_profile(v, k) for k, v in initial_profiles(seed, dual).items()}
        self.active = seed.get('strategy', 'balanced')
        if self.active not in STRATEGIES:
            self.active = 'balanced'

    def __getattr__(self, key):
        return getattr(self.inner, key)

    def load_preferences(self, strategy=None):
        return deepcopy(self.profiles[strategy or self.active])

    def select_strategy(self, strategy, *, checkpoint=lambda: None):
        checkpoint();self.active = strategy;checkpoint()

    def save_preferences(self, value, *, save_plan=False, checkpoint=lambda: None, **kwargs):
        checkpoint()
        key = value.get('strategy', self.active)
        previous = self.profiles[key]
        assert value.get('_revision', previous['_revision']) == previous['_revision']
        current = {**previous, **{k: deepcopy(v) for k, v in value.items() if save_plan or k not in PLAN_FIELDS}}
        if value.get('slot_selection_version') == 2:
            current.pop('selected_slots', None)
        if save_plan:
            current['editable_origin'] = 'saved'
        current['_revision'] = previous['_revision'] + 1
        if key != 'dual_target' and hasattr(self.inner, 'save_preferences'):
            method = self.inner.save_preferences
            parameters = signature(method).parameters
            accepted = kwargs if any(p.kind == Parameter.VAR_KEYWORD for p in parameters.values()) else {
                k: v for k, v in kwargs.items() if k in parameters}
            method(deepcopy(current), **accepted)
        if key == 'dual_target' and hasattr(self.inner, 'dual'):
            self.inner.dual = deepcopy(current)
        checkpoint()
        self.profiles[key] = current
        return deepcopy(current)

    def load_dual_target_preferences(self):
        return self.load_preferences('dual_target')

    def save_dual_target_preferences(self, value, *, guard=None, checkpoint=lambda: None, **kwargs):
        # 与正式保存一致：只拒绝静态换版，库存或所选配装更新照常覆盖并保留原冻结来源。
        static_identity = getattr(self.inner, 'static_identity', None)
        if guard is not None and static_identity is not None and guard.static_identity != static_identity():
            raise ValueError('rewind_source_changed')
        fields = dict(value, strategy='dual_target')
        if 'saved_rewind_shape_ids' in fields:
            fields.update(saved_guard=guard_payload(guard) if guard else None,
                          plan_origin='dual_target' if guard else 'manual')
        updated = self.save_preferences(fields, save_plan='saved_rewind_shape_ids' in value, checkpoint=checkpoint)
        if hasattr(self.inner, 'dual'):
            self.inner.dual = deepcopy(updated)
        return updated
