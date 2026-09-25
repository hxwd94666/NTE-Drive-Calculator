# 冻结空幕分配协议输入，仅转换目录身份和数据类型。
from __future__ import annotations

from dataclasses import asdict


def freeze(request, scorer):
    """No scoring/search in Python: only names, immutable source data and indices."""
    from src.domain.suit_identity import normalized_suit_name, suit_id_for_target

    roles = list(request.roles_db)
    indexes = {name: index for index, name in enumerate(roles)}
    catalog = scorer.stat_catalog
    stats = asdict(catalog)
    stats['tape_main_stat_values'] = stats.pop('tape_main_values')
    stats['tape_main_stats_pool'] = stats.pop('tape_main_stats')
    inventory = [item.model_dump() for item in request.inventory]
    names = set()

    for item in inventory:
        names.update(item.get('sub_stats', {}))
        main = item.get('main_stats')
        names.update(main if isinstance(main, dict) else [str(main or '')])
    for role in request.roles_db.values():
        for field in ('weights', 'main_weights', 'extra_shape_buffs'):
            names.update(role.get(field, {}) or {})
    for pref in request.stat_priority_configs.values():
        if isinstance(pref, dict):
            names.update(pref.get('stats', ()))
            names.update(pref.get('blacklist', ()))
    for mains in request.core_main_filters.values():
        names.update(mains)
    names.update(catalog.gold_base_values)
    names.update(catalog.tape_main_values)
    names.update(catalog.stat_alias_mapping)
    names.update(catalog.stat_alias_mapping.values())
    names.update(name.strip() for name in list(names))
    # Resolve text aliases using the same catalogue (including legacy fuzzy names).
    # Numeric weights, grading, scores and candidate selection remain in Rust.
    normalized = {}
    for name in names:
        normalized[name] = catalog.normalize_stat_name(name, is_percent='%' in name) or name.strip()
    names.update(normalized.values())
    mapping = {
        name: {
            'normal': normalized.get(name) or catalog.normalize_stat_name(name, is_percent='%' in name) or name.strip(),
            'main': catalog.normalize_tape_main_stat(name),
            'alias': catalog.flexible_weight_name(name),
        }
        for name in sorted(names)
    }

    def suit_key(name):
        identity = suit_id_for_target(name, request.sets_db)
        return f'id:{identity}' if identity is not None else f'name:{normalized_suit_name(name)}'

    suit_names = set(request.sets_db)
    suit_names.update(item.get('set_name', '') for item in inventory)
    role_inputs = []
    for role in roles:
        target = request.core_set_targets.get(role) if role in request.core_set_targets else request.module_set_targets.get(role, request.roles_db[role].get('default_set', ''))
        mains = []
        for raw in request.core_main_filters.get(role, ()):
            raw = str(raw or '').strip()
            if raw:
                mains.extend([raw, catalog.normalize_tape_main_stat(raw)])
        role_inputs.append({
            'name': role, 'data': dict(request.roles_db[role]),
            'blueprints': list(request.blueprints_db.get(role, ())),
            'preferences': request.stat_priority_configs.get(role),
            'cap': (request.crit_rate_caps or {}).get(role),
            'target': suit_key(target) if target is not None else None,
            'target_label': str(target or ''), 'mains': list(dict.fromkeys(mains)),
            'main_labels': list(request.core_main_filters.get(role, ())),
        })
    if any(request.property_limits.values()):
        raise ValueError('allocation_v1 is the Calc button path; generic property_limits are not supported')
    if request.strategy != 'role_priority':
        raise ValueError('allocation_v1 requires role_priority')
    return {'batch_kind': 'allocation_v1', 'request': {
        'version': 1, 'inventory': inventory, 'roles': role_inputs,
        'role_order': [indexes[role] for role in request.role_order],
        'groups': [[indexes[role] for role in group if role in indexes] for group in request.priority_groups],
        'stats': stats, 'names': mapping,
        'suit_keys': {name: suit_key(name) for name in suit_names},
        'suit_bucket_keys': {name: normalized_suit_name(name) for name in suit_names},
        'drive_limit': request.drive_screen_limit, 'tape_limit': request.tape_screen_limit,
        'combo_limit': request.blueprint_combo_limit,
    }}
