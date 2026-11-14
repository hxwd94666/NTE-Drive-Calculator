# 兼顾模式独立读取、偏好与保存门禁；与原模式共用正式槽位检查，不改背包或配装。
from __future__ import annotations

from collections.abc import Mapping

from src.domain.rewind.dual_target import DualTargetGuard, DualTargetRequest, RewindInventoryChanged, recommend_dual_targets
from src.domain.rewind.rewind_loadout import RewindSlotReference, positive_id
from src.domain.rewind.rewind_shape_recommendation import GRADE_RATIOS, RewindPlan, RewindShape, target_percentage_score
from src.domain.equipment.loadouts.virtual_equipment import normalized_equipment_assignment
from src.services.rewind.recommendation import RewindLoadoutReader, RewindShapeAnalysis, _owned_shape_counts, snapshot_complete


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


def _validate_guard(service, dao, guard, checkpoint, *, inventory_change_confirmed: bool = False):
    checkpoint()
    if guard.static_identity != service.static_identity():
        raise ValueError('静态资料已变化，请重新生成推荐。')
    # 原来源完整性始终是硬门禁；确认仅允许执行旧八槽，不补齐或重绑原推荐。
    if not snapshot_complete(dao.inventory_snapshot_summary(guard.snapshot_id)):
        raise ValueError('当前库存来源已变化，请重新生成推荐。')
    for ref, source in guard.slots:
        checkpoint()
        slot = dao.get_loadout_slot(ref.slot_id)
        plan = (slot or {}).get('current_plan') or {}
        if (slot is None or slot.get('is_archived') or slot['character_id'] != ref.character_id
                or plan.get('plan_id') != ref.plan_id or plan.get('source_snapshot_id') != source
                or not snapshot_complete(dao.inventory_snapshot_summary(source))):
            raise ValueError('所选配装或来源已变化，请重新生成推荐。')
    # 先核对静态与槽位，避免仅凭库存警告放行同时发生的其他来源问题。
    if not inventory_change_confirmed and dao.current_inventory_snapshot_id() != guard.snapshot_id:
        raise RewindInventoryChanged('当前库存来源已变化，请重新生成推荐。')
    checkpoint()


def validate_guard(service, raw, *, checkpoint=lambda: None, inventory_change_confirmed: bool = False):
    guard = raw if isinstance(raw, DualTargetGuard) else parse_guard(raw)
    with service._user_dao_factory(service._user_database_path) as dao, dao.read_consistent_state():
        _validate_guard(service, dao, guard, checkpoint, inventory_change_confirmed=inventory_change_confirmed)


def analyze(service, request: DualTargetRequest, *, checkpoint=lambda: None):
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
            reader, selected, used = RewindLoadoutReader(dao, shape_rows), [], set()
            for ref in request.improve.slots + request.push.slots:
                checkpoint()
                slot = dao.get_loadout_slot(ref.slot_id)
                plan = (slot or {}).get('current_plan') or {}
                if (slot is None or slot.get('is_archived') or slot['character_id'] != ref.character_id
                        or plan.get('plan_id') != ref.plan_id or ref.plan_id is None):
                    raise ValueError(f'角色 {ref.character_id} 的所选槽位已变化，请重新选择。')
                inspected = reader.inspect(slot)
                if inspected.state != 'ready':
                    raise ValueError(f'角色 {ref.character_id} 的“{inspected.slot_name}”槽位：{inspected.reason}')
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
    validate_guard(service, guard, checkpoint=checkpoint)
    recommendations = result.recommendations
    plans = (RewindPlan('dual_target', '兼顾模式', recommendations,
                       sum(row.priority_score * row.quantity for row in recommendations), result.total_cost),) if recommendations else ()
    return RewindShapeAnalysis(snapshot, str(summary.get('source') or ''), len(shapes), request.selection_limit,
        recommendations, plans=plans, strategy='dual_target',
        required_count=sum(len(group.items) for group in result.groups), owned_shape_counts=tuple(sorted(owned.items())),
        selected_slots=tuple(row.reference for row in selected), static_identity=identity,
        selected_source_snapshots=tuple((row.reference.slot_id, row.source_snapshot_id) for row in selected),
        dual_target=result, dual_guard=guard)
