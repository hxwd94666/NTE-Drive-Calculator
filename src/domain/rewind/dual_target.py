# 双组驱动目标的不可变输入、归一缺口与共同形状覆盖启发式；不读取库存或复制评分公式。
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import isfinite
from typing import Callable

from src.domain.rewind.rewind_loadout import RewindSlotReference, RewindSlotSummary
from src.domain.rewind.rewind_shape_recommendation import (
    GRADE_RATIOS, RewindPricingRule, RewindShape, RewindShapeRecommendation,
    target_grade_score, target_percentage_score,
)


SOURCE_CHANGE_KINDS = frozenset({'inventory', 'plan_replaced', 'slot_unavailable', 'source_unavailable'})


@dataclass(frozen=True, slots=True)
class RewindSourceChange:
    """生成推荐后的一项依据更新；inventory 不带角色身份，其余按所选槽位定位。"""

    kind: str
    character_id: int | None = None
    slot_id: int | None = None

    def __post_init__(self):
        if self.kind not in SOURCE_CHANGE_KINDS or (self.kind == 'inventory') != (self.character_id is None):
            raise ValueError('倒带来源变化资料无效。')


class RewindSourceChanged(ValueError):
    """库存或所选配装在生成后已更新；保存照常覆盖，执行须由用户按本次所列变化确认。"""

    def __init__(self, changes):
        super().__init__('推荐生成后库存或所选配装已更新，请重新生成推荐。')
        self.changes = frozenset(changes)


@dataclass(frozen=True, slots=True)
class GroupTarget:
    slots: tuple[RewindSlotReference, ...]
    grade: str = 'S'
    custom_percent: float | None = None

    def __post_init__(self):
        if not isinstance(self.slots, tuple) or not self.slots or any(not isinstance(ref, RewindSlotReference) for ref in self.slots):
            raise ValueError('每组至少选择一名角色及其保存槽位。')
        if len({ref.character_id for ref in self.slots}) != len(self.slots):
            raise ValueError('每名角色只能选择一个槽位。')
        if self.grade not in GRADE_RATIOS:
            raise ValueError('未知的驱动目标等级。')
        if self.custom_percent is not None:
            target_percentage_score(self.custom_percent, 1)

    def threshold(self, area):
        return (target_percentage_score(self.custom_percent, area) if self.custom_percent is not None
                else target_grade_score(self.grade, area))


@dataclass(frozen=True, slots=True)
class DualTargetRequest:
    improve: GroupTarget
    push: GroupTarget
    selection_limit: int = 8

    def __post_init__(self):
        if not isinstance(self.improve, GroupTarget) or not isinstance(self.push, GroupTarget):
            raise ValueError('两组目标资料无效。')
        if {ref.character_id for ref in self.improve.slots} & {ref.character_id for ref in self.push.slots}:
            raise ValueError('低分组和高分组不能包含同一角色。')
        if type(self.selection_limit) is not int or not 1 <= self.selection_limit <= 8:
            raise ValueError('共同候选槽数应在 1～8 之间。')


@dataclass(frozen=True, slots=True)
class DriveShortfall:
    reference: RewindSlotReference
    source_snapshot_id: int
    shape_id: str
    area: int
    saved_score: float
    target_score: float
    normalized_gap: float


@dataclass(frozen=True, slots=True)
class ShapeGroupDemand:
    shape_id: str
    count: int
    normalized_gap: float
    character_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class GroupShortfalls:
    target: GroupTarget
    demands: tuple[ShapeGroupDemand, ...]
    items: tuple[DriveShortfall, ...]
    examined_count: int

    @property
    def satisfied(self):
        return not self.items


@dataclass(frozen=True, slots=True)
class SharedShapeDemand:
    shape_id: str
    improve: ShapeGroupDemand
    push: ShapeGroupDemand
    weight: float
    total_gap: float


@dataclass(frozen=True, slots=True)
class DualTargetResult:
    request: DualTargetRequest
    groups: tuple[GroupShortfalls, GroupShortfalls]
    shared: tuple[SharedShapeDemand, ...]
    recommendations: tuple[RewindShapeRecommendation, ...]
    uncovered_shapes: tuple[str, ...]
    empty_slots: int
    total_cost: float


@dataclass(frozen=True, slots=True)
class DualTargetGuard:
    snapshot_id: int
    static_identity: tuple[str, int, int]
    # 每项同时冻结槽位、方案和其来源；不以当前背包补全旧方案。
    slots: tuple[tuple[RewindSlotReference, int], ...]


def _group_shortfalls(target, selected, shapes, checkpoint):
    items, examined = [], 0
    by_shape = defaultdict(list)
    for ref in sorted(target.slots, key=lambda row: row.character_id):
        checkpoint()
        slot = selected.get(ref.character_id)
        if slot is None or slot.reference != ref or slot.state != 'ready' or not slot.drives:
            raise ValueError('所选槽位缺少完整、可评分的驱动资料。')
        if type(slot.source_snapshot_id) is not int or slot.source_snapshot_id <= 0:
            raise ValueError('保存方案缺少来源快照。')
        for drive in slot.drives:
            checkpoint()
            shape = shapes.get(drive.shape_id)
            if (shape is None or type(drive.area) is not int or drive.area != shape.cell_count or drive.area <= 0
                    or isinstance(drive.score, bool) or not isinstance(drive.score, (int, float))
                    or not isfinite(drive.score) or drive.score < 0):
                raise ValueError('驱动的形状、面积或逐件评分资料无效。')
            examined += 1
            threshold = target.threshold(drive.area)
            # 单件满分基数为面积 × 10；只累计正缺口，不拿大面积原始分差挤掉小面积。
            gap = max(0., threshold - drive.score) / (drive.area * 10.)
            if gap > 0:
                shortfall = DriveShortfall(ref, slot.source_snapshot_id, drive.shape_id,
                    drive.area, drive.score, threshold, gap)
                items.append(shortfall)
                by_shape[drive.shape_id].append(shortfall)
    demands = tuple(ShapeGroupDemand(shape_id, len(rows), sum(row.normalized_gap for row in rows),
        tuple(sorted({row.reference.character_id for row in rows})))
        for shape_id, rows in sorted(by_shape.items()))
    return GroupShortfalls(target, demands, tuple(items), examined)


def recommend_dual_targets(request: DualTargetRequest, shapes: tuple[RewindShape, ...],
                           slots: tuple[RewindSlotSummary, ...], *, owned_counts=None,
                           pricing=RewindPricingRule(), checkpoint: Callable[[], None] = lambda: None):
    checkpoint()
    shape_map = {shape.shape_id: shape for shape in shapes}
    if len(shape_map) != len(shapes) or any(type(row.cell_count) is not int or row.cell_count <= 0 for row in shapes):
        raise ValueError('正式形状资料无效。')
    selected = {slot.reference.character_id: slot for slot in slots}
    expected = {ref.character_id for group in (request.improve, request.push) for ref in group.slots}
    if len(selected) != len(slots) or set(selected) != expected:
        raise ValueError('两组槽位资料不完整或重复。')
    groups = tuple(_group_shortfalls(group, selected, shape_map, checkpoint) for group in (request.improve, request.push))
    maps = [{row.shape_id: row for row in group.demands} for group in groups]
    totals = [sum(row.normalized_gap for row in group.demands) for group in groups]
    # 先转各组内部占比再取较小值；一组人数更多不等于它天然拥有更高共同权重。
    shared = tuple(sorted((SharedShapeDemand(shape_id, maps[0][shape_id], maps[1][shape_id],
        min(maps[0][shape_id].normalized_gap / totals[0], maps[1][shape_id].normalized_gap / totals[1]),
        maps[0][shape_id].normalized_gap + maps[1][shape_id].normalized_gap)
        for shape_id in maps[0].keys() & maps[1].keys()), key=lambda row: (-row.weight, -row.total_gap, row.shape_id)))
    chosen = shared[:request.selection_limit]
    quantities = Counter({row.shape_id: 1 for row in chosen})
    while chosen and sum(quantities.values()) < request.selection_limit:
        checkpoint()
        eligible = [row for row in chosen if quantities[row.shape_id] < row.improve.count + row.push.count]
        if not eligible:
            break
        def marginal(row):
            count = quantities[row.shape_id]
            # 增加重复份数会提高该形状全部槽位的单价，因此用总费用差而不是单槽标价。
            extra_cost = pricing.cost_for_quantity(count + 1) - pricing.cost_for_quantity(count)
            return row.weight / (count + 1) / extra_cost
        winner = min(eligible, key=lambda row: (-marginal(row), -row.total_gap, row.shape_id))
        quantities[winner.shape_id] += 1
    recommendations = tuple(RewindShapeRecommendation(shape_map[row.shape_id], row.improve.count + row.push.count,
        int((owned_counts or {}).get(row.shape_id, 0)), row.weight, quantities[row.shape_id], row.total_gap) for row in chosen)
    checkpoint()
    return DualTargetResult(request, groups, shared, recommendations,
        tuple(row.shape_id for row in shared[request.selection_limit:]),
        request.selection_limit - sum(quantities.values()),
        sum(pricing.cost_for_quantity(row.quantity) for row in recommendations))
