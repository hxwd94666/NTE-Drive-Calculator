# 冻结并提交单策略倒带偏好，草稿不覆盖已保存八槽；每次写入校验账号回调及修订。
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from uuid import uuid4

from src.domain.equipment.geometry.shapes import OFFICIAL_SHAPE_LABELS
from src.domain.rewind.rewind_loadout import positive_id
from src.domain.rewind.strategy_preferences import (EXECUTION_FIELDS, META_FIELDS, PLAN_FIELDS,
    TEN_LIMIT_MAXIMUM, TEN_LIMIT_QUALITIES, UI_KEY, flatten_profile, strategy_key)


def load(service, strategy=None):
    with service._user_dao_factory(service._user_database_path) as dao:
        values = dao.list_application_setting_copies()
    strategy = strategy or values[UI_KEY]['strategy']
    return flatten_profile(values[strategy_key(strategy)], strategy)


def select(service, strategy, checkpoint):
    strategy_key(strategy)
    with service._user_dao_factory(service._user_database_path) as dao, dao.write_transaction():
        checkpoint()
        dao.patch_application_setting_copy(UI_KEY, dict(strategy=strategy))
        checkpoint()


def _validate_slots(value, dao):
    if 'slot_selection_version' not in value:
        return
    version = value['slot_selection_version']
    maps = ({'legacy': value.get('selected_slots')} if type(version) is int and version == 1
            else value.get('selected_slots_by_strategy') if type(version) is int and version == 2 else None)
    if not isinstance(maps, dict) or (version == 2 and set(maps) != {'balanced', 'focused'}):
        raise ValueError('invalid_rewind_slots')
    for slots in maps.values():
        if not isinstance(slots, dict):
            raise ValueError('invalid_rewind_slots')
        for identifier, slot_id in slots.items():
            if positive_id(identifier) is None or (slot_id is not None and (type(slot_id) is not int or slot_id <= 0)):
                raise ValueError('invalid_rewind_slots')
            slot = dao.get_loadout_slot(slot_id) if slot_id is not None else None
            if slot is not None and slot['character_id'] != int(identifier):
                raise ValueError('invalid_rewind_slots')


def _validate_ten_limits(value):
    """十连次数上限只接受蓝紫金的正整数；0 或不填由界面省略，不写入无效值。"""
    if 'rewind_ten_limits' not in value:
        return
    limits = value['rewind_ten_limits']
    if not isinstance(limits, dict) or any(
            key not in TEN_LIMIT_QUALITIES or type(count) is not int or not 0 < count <= TEN_LIMIT_MAXIMUM
            for key, count in limits.items()):
        raise ValueError('invalid_rewind_profile')


def _plan(value, previous, analysis, guard):
    ids = value.get('saved_rewind_shape_ids')
    if not isinstance(ids, list) or len(ids) != 8 or any(not isinstance(i, str) or i not in OFFICIAL_SHAPE_LABELS for i in ids):
        raise ValueError('invalid_rewind_plan')
    rows = value.get('saved_rewind_slots')
    if rows is not None and (not isinstance(rows, list) or len(rows) != 8 or any(
            not isinstance(row, dict) or row.get('shape_id') != shape or
            isinstance(row.get('quality_gap'), bool) or not isinstance(row.get('quality_gap'), (int, float)) or
            not isfinite(row['quality_gap']) or row['quality_gap'] < 0 for row, shape in zip(rows, ids))):
        raise ValueError('invalid_rewind_plan')
    result = {k: deepcopy(v) for k, v in value.items() if k in PLAN_FIELDS}
    if analysis is not None:
        result['plan_origin'] = 'dual_target' if analysis.dual_target is not None else 'generated'
    else:
        result.setdefault('plan_origin', 'manual')
    result['saved_at_utc'] = datetime.now(timezone.utc).isoformat()
    if guard is not None:
        from src.services.rewind.dual_recommendation import guard_payload
        result.setdefault('saved_guard', guard_payload(guard))
    if analysis is not None:
        result['saved_inputs'] = dict(recommendation_id=uuid4().hex, strategy=analysis.strategy,
            snapshot_id=analysis.snapshot_id, static_identity=list(analysis.static_identity),
            source_snapshots=[list(pair) for pair in analysis.selected_source_snapshots],
            slots=[dict(character_id=r.character_id, slot_id=r.slot_id, plan_id=r.plan_id) for r in analysis.selected_slots],
            # 保存 UI 已冻结的有效请求；UI 的输入修订门禁禁止把改变后的草稿用于旧结果。
            request={k: deepcopy(v) for k, v in value.items() if k not in PLAN_FIELDS | EXECUTION_FIELDS | META_FIELDS},
            algorithm='rewind-shape-v1')
        sources = dict(analysis.selected_source_snapshots)
        if analysis.snapshot_id and analysis.static_identity and analysis.selected_slots:
            result['saved_guard'] = dict(snapshot_id=analysis.snapshot_id, static_identity=list(analysis.static_identity),
                slots=[dict(character_id=r.character_id, slot_id=r.slot_id, plan_id=r.plan_id,
                            source_snapshot_id=sources[r.slot_id]) for r in analysis.selected_slots])
    elif guard is None and result.get('plan_origin') == 'manual':
        result.pop('saved_inputs', None)
        result['saved_guard'] = None
    elif previous.get('saved_inputs') and 'saved_inputs' not in result:
        result['saved_inputs'] = deepcopy(previous['saved_inputs'])
    return result


def save(service, value, *, strategy=None, save_plan=False, analysis=None, guard=None, checkpoint=lambda: None):
    fields = deepcopy(value)
    if not isinstance(fields, dict):
        raise ValueError('invalid_rewind_profile')
    strategy = strategy or fields.get('strategy') or load(service)['strategy']
    key = strategy_key(strategy)
    if analysis is not None:
        # 生成结果的冻结来源必须在最终事务内再次核对，不能只依赖 UI 提交前的独立读取。
        from src.domain.rewind.dual_target import DualTargetGuard
        guard = analysis.dual_guard or DualTargetGuard(analysis.snapshot_id, analysis.static_identity,
            tuple((r, dict(analysis.selected_source_snapshots)[r.slot_id]) for r in analysis.selected_slots))
    with service._user_dao_factory(service._user_database_path) as dao, dao.write_transaction():
        checkpoint()
        current = dao.list_application_setting_copies()[key]
        flatten_profile(current, strategy)
        if fields.get('_revision', current['revision']) != current['revision']:
            raise ValueError('rewind_preference_conflict')
        if '_revision' in fields and type(fields['_revision']) is not int:
            raise ValueError('invalid_rewind_profile')
        if save_plan and analysis is None and guard is None and fields.get('plan_origin') != 'manual' and current['saved_plan'].get('saved_guard'):
            from src.services.rewind.dual_recommendation import parse_guard
            guard = parse_guard(current['saved_plan']['saved_guard'])
        _validate_slots(fields, dao)
        _validate_ten_limits(fields)
        if 'groups' in fields:
            from src.services.rewind.dual_recommendation import _validate_group_preferences
            _validate_group_preferences(fields, dao)
        # 保存只覆盖本策略八槽并原样记录生成时的冻结来源；库存或所选配装此后更新不阻止保存，
        # 由执行入口按变化请求确认。静态换版会使形状解释失效，仍拒绝。
        if guard is not None and guard.static_identity != service.static_identity():
            raise ValueError('rewind_source_changed')
        updated = deepcopy(current)
        updated['draft'].update({k: v for k, v in fields.items() if k not in PLAN_FIELDS | EXECUTION_FIELDS | META_FIELDS})
        if fields.get('slot_selection_version') == 2:
            updated['draft'].pop('selected_slots', None)
        updated['execution_options'].update({k: v for k, v in fields.items() if k in EXECUTION_FIELDS})
        if save_plan:
            updated['saved_plan'] = _plan(fields, current['saved_plan'], analysis, guard)
            updated['draft']['editable_origin'] = 'saved'
        updated.update(revision=current['revision'] + 1, seed='edited')
        dao.compare_and_replace_application_setting_copy(key, updated, expected=current, missing_value={}, check=checkpoint)
        checkpoint()
        if guard is not None and guard.static_identity != service.static_identity():
            raise ValueError('rewind_source_changed')
    return flatten_profile(updated, strategy)
