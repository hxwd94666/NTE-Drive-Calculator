# 兼顾模式独立读取、偏好与保存门禁；与原模式共用正式槽位检查，不改背包或配装。
from __future__ import annotations

from collections.abc import Mapping

from src.domain.rewind.dual_target import (DualTargetGuard, DualTargetRequest, RewindSourceChange, RewindSourceChanged,
    recommend_dual_targets)
from src.domain.rewind.rewind_loadout import RewindSlotReference, positive_id
from src.domain.rewind.shape_overview import slot_drive_margins
from src.domain.rewind.rewind_shape_recommendation import GRADE_RATIOS, RewindPlan, RewindShape, target_percentage_score
from src.domain.equipment.loadouts.virtual_equipment import normalized_equipment_assignment
from src.domain.localization.catalog import localized_text
from src.services.rewind.recommendation import (RewindLoadoutReader, RewindShapeAnalysis, _owned_shape_counts,
    rewind_role_names, snapshot_complete)


def _validate_group_preferences(value, dao):
    groups = value.get('groups')
    if groups is None:
        return
    if not isinstance(groups, dict) or set(groups) != {'improve', 'push'}:
        raise ValueError('兼顾模式分组资料无效。')
    seen = set()
    for group in groups.values():
        if (not isinstance(group, dict) or group.get('grade') not in GRADE_RATIOS
                or group.get('mode') not in {'grade', 'custom'} or not isinstance(group.get('slots'), dict)):
            raise ValueError('兼顾模式目标或槽位资料无效。')
        custom = group.get('custom_percent')
        if custom is not None:
            target_percentage_score(custom, 1)
        for key, slot_id in group['slots'].items():
            identifier = positive_id(key)
            if identifier is None or identifier in seen or (slot_id is not None and (type(slot_id) is not int or slot_id <= 0)):
                raise ValueError('兼顾模式角色重复或槽位身份无效。')
            seen.add(identifier)
            slot = dao.get_loadout_slot(slot_id) if slot_id is not None else None
            if slot is not None and slot['character_id'] != identifier:
                raise ValueError('槽位不属于所选角色。')


def guard_payload(guard):
    return dict(snapshot_id=guard.snapshot_id, static_identity=list(guard.static_identity),
        slots=[dict(character_id=ref.character_id, slot_id=ref.slot_id, plan_id=ref.plan_id,
                    source_snapshot_id=source) for ref, source in guard.slots])


def parse_guard(raw):
    if not isinstance(raw, Mapping):
        raise ValueError('推荐缺少冻结来源，请重新生成并保存。')
    try:
        identity = raw['static_identity']
        if (not isinstance(identity, (tuple, list)) or len(identity) != 3 or not isinstance(identity[0], str)
                or any(type(i) is not int or i < 0 for i in identity[1:])
                or type(raw['snapshot_id']) is not int or raw['snapshot_id'] <= 0):
            raise ValueError('invalid guard')
        slots = tuple((RewindSlotReference(row['character_id'], row['slot_id'], row['plan_id']), row['source_snapshot_id'])
                      for row in raw['slots'])
        if (not slots or any(ref.plan_id is None or type(source) is not int or source <= 0 for ref, source in slots)
                or len({ref.character_id for ref, _ in slots}) != len(slots)
                or len({ref.slot_id for ref, _ in slots}) != len(slots)):
            raise ValueError('invalid guard')
        return DualTargetGuard(raw['snapshot_id'], tuple(identity), slots)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('推荐冻结资料无效，请重新生成并保存。') from exc


def require_static_identity(service, guard):
    """静态形状与面积是八槽的解释基础；换版后旧形状 ID 不能继续保存或执行。"""
    if guard.static_identity != service.static_identity():
        raise ValueError('静态资料已变化，请重新生成推荐。')


def _source_changes(service, dao, guard, checkpoint):
    """只列出生成后已更新的依据，不读取新库存补全或重绑原推荐。

    生成时的库存快照只记录身份，可被保留策略清理，因此不再要求其仍存在。
    """
    checkpoint()
    require_static_identity(service, guard)
    changes = []
    for ref, source in guard.slots:
        checkpoint()
        slot = dao.get_loadout_slot(ref.slot_id)
        plan = (slot or {}).get('current_plan') or {}
        if slot is None or slot.get('is_archived') or slot['character_id'] != ref.character_id:
            kind = 'slot_unavailable'
        elif plan.get('plan_id') != ref.plan_id:
            kind = 'plan_replaced'
        elif plan.get('source_snapshot_id') != source or not snapshot_complete(dao.inventory_snapshot_summary(source)):
            kind = 'source_unavailable'
        else:
            continue
        changes.append(RewindSourceChange(kind, ref.character_id, ref.slot_id))
    if dao.current_inventory_snapshot_id() != guard.snapshot_id:
        changes.append(RewindSourceChange('inventory'))
    checkpoint()
    return frozenset(changes)


def validate_guard(service, raw, *, checkpoint=lambda: None, accepted_changes=frozenset()):
    """静态变化硬拒绝；其余变化须全部在本次已确认集合内，新出现的变化重新请求确认。"""
    guard = raw if isinstance(raw, DualTargetGuard) else parse_guard(raw)
    with service._user_dao_factory(service._user_database_path) as dao, dao.read_consistent_state():
        changes = _source_changes(service, dao, guard, checkpoint)
    if not changes <= frozenset(accepted_changes or ()):
        raise RewindSourceChanged(changes)


def analyze(service, request: DualTargetRequest, *, checkpoint=lambda: None, text_catalog=None):
    if not isinstance(request, DualTargetRequest):
        raise ValueError('兼顾模式请求无效。')
    checkpoint()
    if not service._user_database_path.is_file():
        raise ValueError('尚无保存方案，请先计算并保存。')
    identity = service.static_identity()
    with service._static_dao_factory(service._static_database_path) as static, service._user_dao_factory(service._user_database_path) as dao:
        shape_rows = static.list_shapes()
        shapes = tuple(RewindShape(str(row['shape_id']), int(row['cell_count'])) for row in shape_rows)
        with dao.read_consistent_state():
            snapshot = dao.current_inventory_snapshot_id()
            summary = dao.inventory_snapshot_summary(snapshot) if snapshot is not None else None
            if not snapshot_complete(summary):
                raise ValueError('当前库存资料不完整，请先同步背包。')
            owned = _owned_shape_counts(dao.inventory_module_geometry_counts(snapshot), {row.shape_id for row in shapes})
            reader, selected, used = RewindLoadoutReader(dao, shape_rows, text_catalog=text_catalog), [], set()
            # 提示与原两模式共用文案键；角色只显示名称，身份判断仍用正式 ID。
            role_names = rewind_role_names(static, dao)
            for ref in request.improve.slots + request.push.slots:
                checkpoint()
                name = role_names.get(ref.character_id, str(ref.character_id))
                slot = dao.get_loadout_slot(ref.slot_id)
                plan = (slot or {}).get('current_plan') or {}
                if (slot is None or slot.get('is_archived') or slot['character_id'] != ref.character_id
                        or plan.get('plan_id') != ref.plan_id or ref.plan_id is None):
                    raise ValueError(localized_text(text_catalog, 'rewind.recommendation.problem.plan_changed',
                        '{name} 的所选配装已变化，请关闭并重新打开倒带推荐后选择。', name=name))
                inspected = reader.inspect(slot)
                if inspected.state != 'ready':
                    raise ValueError(localized_text(text_catalog, 'rewind.recommendation.problem.slot_state',
                        '{name} 的“{slot}”槽位{reason}', name=name, slot=inspected.slot_name, reason=inspected.reason))
                for raw in plan.get('assignments') or ():
                    row = normalized_equipment_assignment(raw)
                    if row.get('kind') != 'module':
                        continue
                    uid = row['uid_slot'], row['uid_serial']
                    if uid in used:
                        raise ValueError('所选方案重复使用同一真实驱动，请调整槽位。')
                    used.add(uid)
                selected.append(inspected)
            guard = DualTargetGuard(snapshot, identity, tuple((row.reference, row.source_snapshot_id) for row in selected))
    result = recommend_dual_targets(request, shapes, tuple(selected), owned_counts=owned, checkpoint=checkpoint)
    # 两组各按自己的冻结目标给出逐件余量；只读总览用，不参与共同形状算法。
    by_role = {row.reference.character_id: row for row in selected}
    margins = tuple(margin for key, target in (('improve', request.improve), ('push', request.push))
                    for ref in target.slots for margin in slot_drive_margins(by_role[ref.character_id], target.threshold, key))
    validate_guard(service, guard, checkpoint=checkpoint)
    recommendations = result.recommendations
    plans = (RewindPlan('dual_target', '兼顾模式', recommendations,
                       sum(row.priority_score * row.quantity for row in recommendations), result.total_cost),) if recommendations else ()
    return RewindShapeAnalysis(snapshot, str(summary.get('source') or ''), len(shapes), request.selection_limit,
        recommendations, plans=plans, strategy='dual_target',
        required_count=sum(len(group.items) for group in result.groups), owned_shape_counts=tuple(sorted(owned.items())),
        selected_slots=tuple(row.reference for row in selected), static_identity=identity,
        selected_source_snapshots=tuple((row.reference.slot_id, row.source_snapshot_id) for row in selected),
        dual_target=result, dual_guard=guard, drive_margins=margins)
